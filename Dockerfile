FROM python:3.11-slim

# Prevent Python from writing .pyc and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Cloud Run defaults to port 8080
ENV PORT=8080
EXPOSE 8080

# Run with Gunicorn (1 worker, multi-threaded for Flask + background executor)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
