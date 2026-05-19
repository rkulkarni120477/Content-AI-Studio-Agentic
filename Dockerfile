# =============================================================================
# Content AI Studio — Multi-stage Dockerfile
#
# Stages:
#   base        System dependencies shared by all stages
#   development Hot-reload uvicorn server for local development
#   production  Optimised production image
#
# Usage:
#   Production build:  docker build --target production -t contentai-api .
#   Dev build:         docker build --target development -t contentai-api:dev .
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: base — shared OS dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

# System packages needed by psycopg2, pypdf, and python-docx.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Add /app to PYTHONPATH so both `app` and `promptops_app` packages are importable.
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# Stage 2: development — hot-reload with volume mounts
# ---------------------------------------------------------------------------
FROM base AS development

COPY requirements.txt pyproject.toml ./
# Install everything including dev extras.
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir httpx pytest pytest-asyncio pytest-cov ruff

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# Stage 3: production — lean image, no dev tools
# ---------------------------------------------------------------------------
FROM base AS production

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Uvicorn with 2 workers. For high-traffic: increase --workers or use Gunicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
