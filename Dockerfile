# OPS CONTROL - Dockerfile
# Multi-stage build for a lean production image.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="OPS CONTROL"
LABEL org.opencontainers.image.description="Discord operations bot for OPS ROOM aviation platform"
LABEL org.opencontainers.image.version="1.0.0"

# Prevent .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

# Install system dependencies required by Pillow
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libfreetype6-dev \
        liblcms2-dev \
        libwebp-dev \
        libharfbuzz-dev \
        libfribidi-dev \
        libxcb1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY assets/ ./assets/

# Create directories for runtime data
RUN mkdir -p /app/data /app/logs /app/assets/generated

# Run as non-root user
RUN useradd --create-home --shell /bin/bash opscontrol && \
    chown -R opscontrol:opscontrol /app
USER opscontrol

# Entry point
CMD ["python", "-m", "bot.main"]
