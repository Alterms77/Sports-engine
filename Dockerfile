# ─────────────────────────────────────────────────────────────────────────────
# Sports Engine — Production Dockerfile
#
# Build:  docker build -t sports-engine .
# Run:    docker run --env-file .env sports-engine
#
# Requirements:
#   .env file with at least:
#     TOKEN=<your Telegram bot token>
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: base image ───────────────────────────────────────────────────────
FROM python:3.11-slim AS base

LABEL maintainer="Sports Engine"
LABEL description="Multi-sport Telegram bot with live data from SofaScore/TheSportsDB/ESPN"

# Install OS-level dependencies (ca-certificates for HTTPS, tzdata for timezones)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone to UTC for consistent timestamps
ENV TZ=UTC

# ── Stage 2: Python dependencies ─────────────────────────────────────────────
FROM base AS deps

WORKDIR /app

# Copy only requirements first to leverage Docker layer cache
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Stage 3: application ──────────────────────────────────────────────────────
FROM deps AS app

WORKDIR /app

# Copy the full project
COPY sports_engine/ ./sports_engine/

# ── Python path setup ─────────────────────────────────────────────────────────
# sports_engine/ must be on PYTHONPATH so relative imports (api, core, sports, …)
# resolve correctly from any working directory — same logic as bot/bot.py and Procfile.
ENV PYTHONPATH=/app/sports_engine

# ── Non-root user for security ────────────────────────────────────────────────
RUN addgroup --system botgroup && adduser --system --ingroup botgroup botuser
USER botuser

# ── Healthcheck: verify the HTTP health endpoint responds ────────────────────
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
CMD ["python", "sports_engine/bot/bot.py"]
