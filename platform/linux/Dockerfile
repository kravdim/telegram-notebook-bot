FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /bin/

WORKDIR /app

RUN groupadd --system --gid 10001 dailyplanner \
    && useradd --system --uid 10001 --gid dailyplanner --home /app dailyplanner

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Locked Python dependencies (application itself is copied afterwards so this
# layer stays cacheable across source-only changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY . .
RUN chmod +x /app/platform/linux/docker-entrypoint.sh \
    && mkdir -p /app/backups \
    && chown -R dailyplanner:dailyplanner /app

ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER 10001:10001

ENTRYPOINT ["/app/platform/linux/docker-entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
