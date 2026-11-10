# Build context is the repository root (see docker-compose.yml): docker build -f backend/Dockerfile .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY backend/app /app/backend/app
COPY config /app/config
COPY seed /app/seed

# /data holds SQLite, downloaded transcripts and the UI settings override (mounted as a volume)
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown -R app /data
USER app
ENV DATA_DIR=/data CONFIG_DIR=/app/config SEED_DIR=/app/seed
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# one worker on purpose: the scheduler runs inside this process
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
