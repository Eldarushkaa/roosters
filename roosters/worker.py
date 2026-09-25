"""Crash-safe settlement worker. Multiple workers are safe but unnecessary.

Run beside the web process with the same DATABASE_PATH and environment.
HTTP requests also recover due battles, so worker outages do not lose payouts.
"""
import logging
import signal
import time
from .config import Settings
from .service import GameService
from .storage import Database


def main():
    settings = Settings.from_env()
    settings.validate()
    database = Database(settings.database_path)
    database.migrate()
    service = GameService(database)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            service.tick()
        except Exception:
            logging.exception("Settlement tick failed; the next tick will retry")
        time.sleep(1)


if __name__ == "__main__":
    main()
