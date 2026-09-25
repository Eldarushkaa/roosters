"""Application factory. Transport concerns stay here, gameplay in GameService."""

import re
import sqlite3
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .auth import Sessions, validate_init_data
from .config import Settings
from .errors import GameError
from .rules import RULES_VERSION
from .service import GameService
from .storage import Database


def create_app(settings=None, *, clock=None, random_float=None):
    settings = settings or Settings.from_env()
    settings.validate()
    web = Path(__file__).resolve().parent.parent / "web"
    app = Flask(__name__, static_folder=str(web), static_url_path="/static")
    app.config.update(MAX_CONTENT_LENGTH=32768, JSON_SORT_KEYS=False)
    app.json.ensure_ascii = False
    database = Database(settings.database_path)
    database.migrate()
    kwargs = {"random_float": random_float}
    if clock is not None:
        kwargs["clock"] = clock
    service = GameService(database, **kwargs)
    sessions = Sessions(settings.secret_key)
    app.extensions.update(game=service, database=database, sessions=sessions)

    @app.before_request
    def origin_guard():
        # No CORS and no cookies. Explicit origin guard also blocks cross-site
        # development login attempts, including local DNS rebinding.
        if settings.allow_dev_auth:
            hostname = urlparse(request.host_url).hostname
            if hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise GameError("development_local_only", "Локальный вход доступен только на localhost.", 403)
        if request.method == "POST":
            origin = request.headers.get("Origin")
            allowed = {request.host_url.rstrip("/")}
            if settings.webapp_url:
                url = urlparse(settings.webapp_url)
                allowed.add("{}://{}".format(url.scheme, url.netloc))
            if origin and origin not in allowed:
                raise GameError("invalid_origin", "Запрос с другого сайта запрещён.", 403)

    @app.after_request
    def response_headers(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Telegram Web embeds Mini Apps in a frame, so X-Frame-Options DENY
        # would break legitimate launches. CSP limits actual resource origins.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'"
        )
        return response

    @app.errorhandler(GameError)
    def game_error(error):
        return jsonify(error={"code": error.code, "message": error.message}), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error={"code": "http_"+str(error.code), "message": error.description}), error.code

    @app.errorhandler(sqlite3.OperationalError)
    def storage_error(error):
        app.logger.error("Database operation failed: %s", type(error).__name__)
        return jsonify(error={"code": "storage_unavailable", "message": "Сервер занят. Повторите ту же команду через несколько секунд."}), 503

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Unhandled server failure")
        return jsonify(error={"code": "internal_error", "message": "Не удалось выполнить команду. Повторите попытку."}), 500

    def body():
        if not request.is_json:
            raise GameError("json_required", "Ожидается application/json.", 415)
        value = request.get_json()
        if not isinstance(value, dict):
            raise GameError("invalid_payload", "Ожидается JSON-объект.")
        return value

    def authenticated(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            scheme, _, token = request.headers.get("Authorization", "").partition(" ")
            if scheme != "Bearer" or not token:
                raise GameError("unauthorized", "Войдите в игру заново.", 401)
            g.player_id = sessions.verify(token)
            if g.player_id.startswith("dev:") and not settings.allow_dev_auth:
                raise GameError("dev_auth_disabled", "Локальный вход отключён.", 403)
            return handler(*args, **kwargs)
        return wrapped

    @app.get("/")
    def index():
        return send_from_directory(web, "index.html")

    @app.get("/healthz")
    def health():
        with database.transaction() as db:
            db.execute("SELECT 1")
        return jsonify(status="ok", rules_version=RULES_VERSION)

    @app.get("/api/v1/config")
    def config():
        return jsonify(dev_auth=settings.allow_dev_auth, bot_username=settings.bot_username, rules_version=RULES_VERSION)

    @app.post("/api/v1/auth/dev")
    def dev_auth():
        if not settings.allow_dev_auth:
            raise GameError("dev_auth_disabled", "Откройте игру через Telegram.", 403)
        payload = body()
        service._fields(payload, ["user_id", "name"])
        user_id, name = payload["user_id"], payload["name"]
        if not isinstance(user_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", user_id):
            raise GameError("invalid_user", "Неверный идентификатор игрока.")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise GameError("invalid_name", "Имя должно содержать от 1 до 64 символов.")
        pid = "dev:" + user_id
        state = service.register(pid, name.strip(), True)
        return jsonify(token=sessions.issue(pid), state=state)

    @app.post("/api/v1/auth/telegram")
    def telegram_auth():
        payload = body()
        service._fields(payload, ["init_data"])
        identity = validate_init_data(payload["init_data"], settings.bot_token, now=service.clock())
        state = service.register(identity["id"], identity["name"], False, identity["referral"])
        return jsonify(token=sessions.issue(identity["id"]), state=state)

    @app.get("/api/v1/state")
    @authenticated
    def state():
        return jsonify(service.state(g.player_id))

    @app.get("/api/v1/leaderboard")
    @authenticated
    def leaderboard():
        return jsonify(service.leaderboard(g.player_id))

    @app.get("/api/v1/battle/quote")
    @authenticated
    def battle_quote():
        if set(request.args) != {"stake_minor"} or len(request.args.getlist("stake_minor")) != 1:
            raise GameError("invalid_payload", "Передайте одну ставку stake_minor.")
        amount = request.args["stake_minor"]
        # Avoid float parsing, duplicate query keys, unbounded integers and
        # acceptance of spaces/signs/exponents as a monetary representation.
        if not re.fullmatch(r"[0-9]{1,16}", amount):
            raise GameError("invalid_stake", "Передайте ставку целым числом сотых долей монеты.")
        return jsonify(service.battle_quote(g.player_id, int(amount)))

    @app.post("/api/v1/<path:operation>")
    @authenticated
    def command(operation):
        return jsonify(service.command(g.player_id, request.headers.get("Idempotency-Key", ""), operation, body()))

    return app
