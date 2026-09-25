"""Validate Telegram initData and issue time-limited bearer sessions."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from itsdangerous import BadSignature, URLSafeTimedSerializer
from .errors import GameError


def validate_init_data(raw, bot_token, now=None, max_age=3600):
    """Verify the original raw query, rejecting duplicates and stale/future data.

    Protocol: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    No identity is ever taken from initDataUnsafe or an unsigned user payload.
    """
    invalid = GameError("invalid_telegram_auth", "Не удалось проверить вход через Telegram.", 401)
    if not bot_token or not isinstance(raw, str) or not raw or len(raw) > 16384:
        raise invalid
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise invalid
        signature = fields.pop("hash")
        check = "\n".join("{}={}".format(k, fields[k]) for k in sorted(fields))
        key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise invalid
        timestamp = int(fields["auth_date"])
        current = time.time() if now is None else now
        if timestamp > current + 30 or current - timestamp > max_age:
            raise invalid
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
            raise invalid
        if user.get("is_bot") or not isinstance(user.get("first_name"), str):
            raise invalid
        return {"id": "tg:{}".format(user["id"]), "name": user["first_name"][:64],
                "is_dev": False, "referral": fields.get("start_param", "")}
    except (KeyError, ValueError, TypeError, UnicodeError):
        raise invalid from None


class Sessions:
    def __init__(self, secret):
        self.signer = URLSafeTimedSerializer(secret, salt="roosters-session-v1")

    def issue(self, player_id):
        return self.signer.dumps({"sub": player_id})

    def verify(self, token):
        try:
            payload = self.signer.loads(token, max_age=86400)
            subject = payload["sub"]
            if not isinstance(subject, str) or not subject.startswith(("dev:", "tg:")):
                raise ValueError()
            return subject
        except (BadSignature, ValueError, KeyError, TypeError):
            raise GameError("unauthorized", "Войдите в игру заново.", 401) from None

