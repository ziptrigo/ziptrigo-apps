# Multi-stage build for users service
# Stage 1: Builder
FROM python:3.13-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements/base.txt requirements/users.txt /app/requirements/

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements/users.txt


# Stage 2: Runtime
FROM python:3.13-slim

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Copy shared packages
COPY shared/ /app/shared/

# Install shared packages
RUN pip install -e /app/shared/utils -e /app/shared/auth_client

# Copy user-service code
COPY user-service/ /app/user-service/

WORKDIR /app/user-service

# Set Python path
ENV PYTHONPATH=/app/user-service:/app/shared/utils:/app/shared/auth_client:$PYTHONPATH

# Expose port
EXPOSE 8010

# Run migrations and start server
# Note: In production, run migrations separately during deployment
CMD ["python", "manage.py", "runserver", "0.0.0.0:8010"]
