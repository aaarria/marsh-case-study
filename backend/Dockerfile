# Marsh Evidence-First Advisory API (FastAPI + LangGraph). Build from the repo root:
#   docker build -f backend/Dockerfile -t marsh-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/storage/cache/models \
    POLICIES_DIR=/app/data/policies \
    STORAGE_DIR=/app/storage \
    OUTPUTS_DIR=/app/outputs \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

COPY backend /app/backend
COPY data /app/data
COPY .env.example /app/.env.example
# Pre-built indexes + metadata (chunks are rebuilt into SQLite at startup if missing)
COPY storage/metadata /app/storage/metadata
COPY storage/vector_index /app/storage/vector_index
COPY storage/bm25_index /app/storage/bm25_index
RUN mkdir -p /app/storage/sqlite /app/storage/cache /app/outputs/presentations /app/outputs/audit_reports \
    && useradd --system --uid 10001 --no-create-home advisory \
    && chown -R advisory:advisory /app/storage /app/outputs

USER advisory
WORKDIR /app/backend
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
