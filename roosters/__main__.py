"""Local entrypoint: python -m roosters. Production uses a WSGI server."""
from . import create_app
from .config import Settings

if __name__ == "__main__":
    settings = Settings.from_env()
    create_app(settings).run(host=settings.host, port=settings.port, debug=False, threaded=True)
