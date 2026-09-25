"""Set the global Telegram chat menu only after an explicit operator command."""

from __future__ import annotations

import logging

from scripts.bot import TelegramAPIError, TelegramClient, validate_webapp_url


def main() -> int:
    from roosters.config import Settings, load_env

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        load_env()
        settings = Settings.from_env()
        url = validate_webapp_url(settings.webapp_url)
        client = TelegramClient(settings.bot_token)
        client.call("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Арена", "web_app": {"url": url}}})
        client.call(
            "setMyCommands",
            {"commands": [
                {"command": "start", "description": "Открыть петушиную арену"},
                {"command": "play", "description": "Играть"},
                {"command": "help", "description": "Помощь"},
            ]},
        )
    except ValueError:
        logging.error("Check BOT_TOKEN and WEBAPP_URL in .env; a public HTTPS URL is required.")
        return 2
    except TelegramAPIError as error:
        logging.error("Telegram menu setup failed (API code %s). It is safe to run this command again.", error.code)
        return 1
    logging.info("Telegram menu and commands configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
