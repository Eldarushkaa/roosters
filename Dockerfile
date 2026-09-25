FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/roosters.sqlite3

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 arena \
    && useradd --uid 10001 --gid arena --no-create-home arena \
    && mkdir /app/data \
    && chown arena:arena /app/data

COPY --chown=arena:arena roosters ./roosters
COPY --chown=arena:arena web ./web
COPY --chown=arena:arena scripts ./scripts

USER arena
EXPOSE 8000
VOLUME ["/app/data"]

CMD ["gunicorn", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "roosters:create_app()"]
