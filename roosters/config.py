"""Explicit environment configuration; imports have no side effects."""
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def load_env(path=".env"):
    """Read KEY=value defaults, without shell evaluation or overriding env."""
    source = Path(path)
    if source.exists():
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    allow_dev_auth: bool = False
    bot_token: str = ""
    bot_username: str = ""
    webapp_url: str = ""
    secret_key: str = ""
    database_path: str = "data/roosters.sqlite3"
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls):
        load_env()
        return cls(app_env=os.getenv("APP_ENV", "development"),
                   allow_dev_auth=os.getenv("ALLOW_DEV_AUTH", "false").lower() == "true",
                   bot_token=os.getenv("BOT_TOKEN", ""), bot_username=os.getenv("BOT_USERNAME", "").lstrip("@"),
                   webapp_url=os.getenv("WEBAPP_URL", ""), secret_key=os.getenv("SECRET_KEY", ""),
                   database_path=os.getenv("DATABASE_PATH", "data/roosters.sqlite3"),
                   host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))

    def validate(self):
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        if len(self.secret_key) < 32 or self.secret_key.startswith("replace-with"):
            raise ValueError("Set SECRET_KEY to a random value of at least 32 characters")
        if self.app_env == "production":
            if self.allow_dev_auth:
                raise ValueError("Development authentication is forbidden in production")
            if not self.bot_token:
                raise ValueError("BOT_TOKEN is required in production")
            url = urlparse(self.webapp_url)
            if url.scheme != "https" or not url.hostname or url.hostname.endswith(".example"):
                raise ValueError("Production WEBAPP_URL must be your public HTTPS URL")

