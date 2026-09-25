"""Small Telegram launcher bot, independent from the game HTTP process.

Run ``python -m scripts.bot`` after filling .env. Importing this module never
contacts Telegram. All game state, rewards and authentication belong to the
server; the bot sends a button that opens the Mini App when its URL is ready.
With an empty/default WEBAPP_URL it replies in bot-only mode, without a dead link.
"""

from __future__ import annotations

import json
import http.client
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)
API_RESPONSE_LIMIT = 1024 * 1024


class TelegramAPIError(Exception):
    """Sanitized API failure; never retain a URL containing the bot token."""

    def __init__(self, code: int = 0, retry_after: int = 0) -> None:
        self.code = code
        self.retry_after = min(max(retry_after, 0), 60)
        self.retryable = code in (0, 408, 429) or code >= 500
        super().__init__("Telegram API request failed (code {}).".format(code))


class TelegramClient:
    """Minimal JSON Bot API transport with bounded reads and safe exceptions."""

    def __init__(self, token: str, opener: Callable[..., Any] = urllib.request.urlopen):
        if not token:
            raise ValueError("BOT_TOKEN is required.")
        self._base_url = "https://api.telegram.org/bot" + token + "/"
        self._opener = opener

    def call(self, method: str, payload: dict, timeout: int = 35) -> Any:
        # Method names are internal constants, never derived from chat messages.
        request = urllib.request.Request(
            self._base_url + method,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read(API_RESPONSE_LIMIT + 1)
        except urllib.error.HTTPError as error:
            # HTTPError.__str__ and urllib URLError reasons can contain secrets.
            # Do not propagate or log either the original exception or response.
            code = error.code
            retry_after = 0
            try:
                error_body = json.loads(error.read(API_RESPONSE_LIMIT + 1)) if error.fp is not None else {}
                if isinstance(error_body, dict) and isinstance(error_body.get("parameters"), dict):
                    value = error_body["parameters"].get("retry_after", 0)
                    retry_after = value if isinstance(value, int) else 0
            except (UnicodeDecodeError, ValueError, OSError, http.client.HTTPException):
                pass
            # Python 3.9 HTTPError may have no attached response file (for
            # example a transport adapter's synthetic error); close() then
            # raises and exposes the original exception in a traceback.
            if error.fp is not None:
                error.close()
            raise TelegramAPIError(code, retry_after) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise TelegramAPIError() from None
        if len(raw) > API_RESPONSE_LIMIT:
            raise TelegramAPIError()
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            raise TelegramAPIError() from None
        if not isinstance(data, dict):
            raise TelegramAPIError()
        if not data.get("ok"):
            code = data.get("error_code", 0)
            parameters = data.get("parameters")
            retry_after = parameters.get("retry_after", 0) if isinstance(parameters, dict) else 0
            raise TelegramAPIError(
                code if isinstance(code, int) else 0,
                retry_after if isinstance(retry_after, int) else 0,
            )
        return data.get("result")


def validate_webapp_url(url: str) -> str:
    """Telegram requires a public HTTPS URL; reject credentials and fragments."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname == "your-domain.example"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("WEBAPP_URL must be a public HTTPS URL without credentials or a fragment.")
    return url


def resolve_webapp_url(url: str) -> str:
    """Allow first-run polling before publishing the game, but reject typos.

    Only an empty value and the template's exact placeholder mean unconfigured.
    Any other supplied value must pass normal Mini App URL validation.
    """
    value = url.strip()
    if not value or value.rstrip("/") == "https://your-domain.example":
        return ""
    return validate_webapp_url(value)


def handle_update(client: TelegramClient, update: dict, webapp_url: str, bot_username: str = "") -> None:
    """Reply only to launcher commands in private chats.

    Telegram Web App keyboard buttons are for private chats. Unknown updates and
    messages are ignored. No user text is copied into outbound messages.
    """
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    body = message.get("text")
    if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(body, str):
        return
    words = body.split()
    if not words:
        return
    command, _, addressed_to = words[0].partition("@")
    if addressed_to and addressed_to.lower() != bot_username.lstrip("@").lower():
        return
    if command not in ("/start", "/help", "/play"):
        return
    if not isinstance(chat.get("id"), int):
        return
    if not webapp_url:
        client.call("sendMessage", {
            "chat_id": chat["id"],
            "text": (
                "🐓 Бот на связи!\n\n"
                "Арена пока не подключена к Telegram. Скоро здесь появится кнопка игры.\n\n"
                "/play — проверить доступность арены, /help — помощь."
            ),
        })
        return
    client.call(
        "sendMessage",
        {
            "chat_id": chat["id"],
            "text": (
                "🐓 Добро пожаловать на петушиную арену!\n\n"
                "Прокачивай петуха, собирай экипировку и сражайся с ботами или игроками. "
                "Это бесплатная игра: игровые монеты нельзя вывести в деньги.\n\n"
                "Нажми кнопку, чтобы открыть игру. /play — открыть арену, /help — помощь."
            ),
            "reply_markup": {
                "inline_keyboard": [[{"text": "🐓 Открыть арену", "web_app": {"url": webapp_url}}]]
            },
        },
    )


class Poller:
    """Long-poll updates, acknowledging each only after successful handling.

    A transient send failure keeps the update pending. A permanent send failure
    (for example a blocked bot) skips that update so it cannot block the queue.
    The offset lives in memory: a restart may repeat one launcher message, which
    is harmless. Game commands never pass through this delivery mechanism.
    """

    def __init__(self, client: TelegramClient, webapp_url: str, bot_username: str = ""):
        self.client = client
        self.webapp_url = resolve_webapp_url(webapp_url)
        self.bot_username = bot_username
        self.offset = 0

    def poll_once(self) -> None:
        updates = self.client.call(
            "getUpdates",
            {"offset": self.offset, "timeout": 25, "allowed_updates": ["message"]},
        )
        if not isinstance(updates, list):
            raise TelegramAPIError()
        for update in updates:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                continue
            update_id = update["update_id"]
            if update_id < self.offset:
                continue
            try:
                handle_update(self.client, update, self.webapp_url, self.bot_username)
            except TelegramAPIError as error:
                if error.retryable or error.code == 401:
                    raise
                LOGGER.warning("Skipping undeliverable launcher reply (API code %s).", error.code)
            self.offset = update_id + 1


def main() -> int:
    """Start polling only when explicitly invoked by the operator."""
    from roosters.config import Settings, load_env

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        load_env()
        settings = Settings.from_env()
        poller = Poller(TelegramClient(settings.bot_token), settings.webapp_url, settings.bot_username)
    except ValueError:
        LOGGER.error("Check BOT_TOKEN and WEBAPP_URL in .env; use a valid HTTPS URL or leave WEBAPP_URL empty.")
        return 2
    LOGGER.info("Telegram long polling started (%s). Press Ctrl+C to stop.",
                "Mini App enabled" if poller.webapp_url else "bot only; Mini App URL not configured")
    backoff = 1
    connected = False
    try:
        while True:
            try:
                poller.poll_once()
                if not connected:
                    LOGGER.info("Telegram polling connected; ready for commands.")
                    connected = True
                backoff = 1
            except TelegramAPIError as error:
                if not error.retryable:
                    LOGGER.error(
                        "Polling stopped (API code %s). Check token, webhook configuration and other bot instances.",
                        error.code,
                    )
                    return 1
                delay = max(backoff, error.retry_after)
                LOGGER.warning("Telegram temporarily unavailable (API code %s); retry in %ss.", error.code, delay)
                time.sleep(delay)
                backoff = min(backoff * 2, 30)
    except KeyboardInterrupt:
        LOGGER.info("Telegram launcher stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
