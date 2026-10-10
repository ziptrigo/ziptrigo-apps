# Multi-stage build for the ZipTrigo site
# Stage 1: Builder
FROM python:3.14-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN python -m pip install --no-cache-dir uv

# Copy locked dependency metadata
COPY pyproject.toml uv.lock /app/

# Install Python dependencies (runtime only; no dev group)
RUN uv sync --frozen --no-install-project


# Stage 2: Runtime
FROM python:3.14-slim

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /app/.venv /app/.venv

# Make sure scripts in the virtualenv are usable
ENV PATH=/app/.venv/bin:$PATH

# Copy the project
COPY manage.py docker-entrypoint.sh supervise.py gunicorn.conf.py /app/
COPY config/ /app/config/
COPY apps/ /app/apps/

# Collect static files so WhiteNoise can serve them when DEBUG is off (it only uses the app
# directories directly in DEBUG). Settings insist on an env file, so give the build a throwaway one.
RUN mkdir -p /app/media && \
    touch .env.dev && \
    ENVIRONMENT=dev python manage.py collectstatic --noinput && \
    rm .env.dev

EXPOSE 8000

# gunicorn reads its worker count from WEB_CONCURRENCY; override it per deployment if needed.
ENV WEB_CONCURRENCY=3

# The entrypoint applies migrations first when RUN_MIGRATIONS=1 (see docker-entrypoint.sh), then
# execs the command, so `supervise.py` becomes PID 1 only after migrations finish and the queue
# worker never starts before them. It runs gunicorn (whose `gunicorn.conf.py` starts the scheduler
# thread in each worker when SCHEDULER_ENABLED) and, unless RUN_TASK_WORKER=0, `manage.py db_worker`.
# docker-compose.yml overrides the command with `runserver` for local development.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "supervise.py"]
