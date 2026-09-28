# syntax=docker/dockerfile:1
#
# Single-service image: compiles the Angular dashboard and serves it from the
# same FastAPI process that handles the GitHub App webhook. One port serves
# the dashboard (/), the REST API (/api/*) and the webhook (/webhook).

# ---------------------------------------------------------------- stage 1
FROM node:22-alpine AS dashboard
WORKDIR /build/dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npx ng build --configuration production

# ---------------------------------------------------------------- stage 2
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Dependencies first so code edits don't bust the pip layer cache.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY api.py github_client.py ./
COPY github_app/ ./github_app/
COPY pr_decomposer/ ./pr_decomposer/

# Compiled dashboard, placed exactly where api.py looks for it.
COPY --from=dashboard /build/dashboard/dist/dashboard/browser/ ./dashboard/dist/dashboard/browser/

# Mount a volume here to persist installations.json, app_tokens.json and
# reports/ across container restarts. Losing these means losing installations.
VOLUME ["/app/data"]

# Run unprivileged.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
