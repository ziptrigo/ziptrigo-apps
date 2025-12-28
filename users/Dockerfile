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
