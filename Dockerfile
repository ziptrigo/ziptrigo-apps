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
COPY manage.py /app/
COPY config/ /app/config/
COPY apps/ /app/apps/

# Collect static files so WhiteNoise can serve them when DEBUG is off (it only uses the app
# directories directly in DEBUG). Settings insist on an env file, so give the build a throwaway one.
RUN mkdir -p /app/media && \
    touch .env.dev && \
    ENVIRONMENT=dev python manage.py collectstatic --noinput && \
    rm .env.dev

EXPOSE 8000

# Run migrations and start server
# Note: In production, run migrations separately during deployment
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
