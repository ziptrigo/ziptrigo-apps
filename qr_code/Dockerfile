# Multi-stage build for qr_code service
# Stage 1: Builder
FROM python:3.13-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements/base.txt requirements/qr_code.txt /app/requirements/

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements/qr_code.txt


# Stage 2: Runtime
FROM python:3.13-slim

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Copy common code
COPY common/ /app/common/

# Copy qr_code app code
COPY qr_code/ /app/qr_code/

WORKDIR /app/qr_code

# Set Python path to include parent directory for common imports
ENV PYTHONPATH=/app:$PYTHONPATH

# Create directories for media and static files
RUN mkdir -p /app/qr_code/media /app/qr_code/staticfiles

# Expose port
EXPOSE 8020

# Run migrations and start server
# Note: In production, run migrations separately during deployment
CMD ["python", "manage.py", "runserver", "0.0.0.0:8020"]
