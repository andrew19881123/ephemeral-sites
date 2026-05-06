# syntax=docker/dockerfile:1.7
# ===================================================================
# ephemeral-sites - multi-stage build
# Stage 1: build deps into a venv with poetry
# Stage 2: slim runtime image (non-root, read-only friendly)
# ===================================================================

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml poetry.lock* ./

RUN pip install "poetry==${POETRY_VERSION}" \
    && poetry install --only main --no-root

# ---------- Runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src

RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -s /sbin/nologin -m app

WORKDIR /app

COPY --from=builder /build/.venv /app/.venv
COPY --chown=app:app src/ /app/src/

USER app

EXPOSE 8080 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=3)" || exit 1

ENTRYPOINT ["python", "-m", "ephemeral_sites"]
CMD ["api"]
