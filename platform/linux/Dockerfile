FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /uvx /bin/

WORKDIR /app

RUN groupadd --system --gid 10001 dailyplanner \
    && useradd --system --uid 10001 --gid dailyplanner --home /app dailyplanner

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3t64 \
    openssl \
    openssl-provider-legacy \
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
