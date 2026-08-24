FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

WORKDIR /app

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
RUN chmod +x /app/platform/linux/docker-entrypoint.sh

ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/platform/linux/docker-entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
