# Multi-stage build for users service
# Stage 1: Builder
FROM python:3.13-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update &&     apt-get install -y --no-install-recommends gcc &&     rm -rf /var/lib/apt/lists/*

# Install uv
RUN python -m pip install --no-cache-dir uv

# Copy locked dependency metadata
COPY pyproject.toml uv.lock /app/

# Install Python dependencies
RUN uv sync --frozen --no-install-project --group users


# Stage 2: Runtime
FROM python:3.13-slim

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /app/.venv /app/.venv

# Make sure scripts in the virtualenv are usable
ENV PATH=/app/.venv/bin:$PATH

# Copy common code
COPY common/ /app/common/

# Copy users app code
COPY users/ /app/users/

WORKDIR /app/users

# Set Python path to include parent directory for common imports
ENV PYTHONPATH=/app:$PYTHONPATH

# Expose port
EXPOSE 8010

# Run migrations and start server
# Note: In production, run migrations separately during deployment
CMD ["python", "manage.py", "runserver", "0.0.0.0:8010"]
