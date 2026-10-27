FROM node:22-alpine AS frontend
WORKDIR /build
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY app/templates app/templates
COPY app/static app/static
COPY scripts/vendor.mjs scripts/vendor.mjs
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools libsndfile1 tini curl && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra test --no-install-project
RUN groupadd --gid 10001 audiolab && useradd --uid 10001 --gid audiolab --create-home audiolab && mkdir -p /data && chown audiolab:audiolab /data
COPY --chown=audiolab:audiolab . .
COPY --from=frontend --chown=audiolab:audiolab /build/app/static/css/app.css app/static/css/app.css
COPY --from=frontend --chown=audiolab:audiolab /build/app/static/vendor app/static/vendor
USER audiolab
ENV RUFF_CACHE_DIR=/tmp/audio-lab-ruff-cache
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
