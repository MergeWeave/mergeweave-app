FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY alembic.ini ./

# Create logs directory
RUN mkdir -p /app/logs

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Configure git identity for merge operations
RUN git config --global user.name "MergeWeave Dispatch Agent" && \
    git config --global user.email "admin@mergeweave.ai"

# Expose port
EXPOSE 8000

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/src

# Health check (docker-compose may override these settings)
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start command
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]