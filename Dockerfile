# Multi-stage build for Church Budget Tool
# Python 3.11 + FastAPI

# --- Stage 1: Install dependencies ---
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ ./src/

# Install the package and its dependencies
RUN pip install --no-cache-dir --prefix=/install .

# --- Stage 2: Production image ---
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/valid-agenda/church-budget"
LABEL org.opencontainers.image.description="Church Budget Tool — FastAPI dashboard"

# Replicate the project layout so config.py path resolution works:
# config.py: Path(__file__).parent.parent.parent / "config"
# /project/src/app/config.py -> /project/src/app -> /project/src -> /project -> /project/config
WORKDIR /project

# Install Python dependencies from builder (excludes the app package itself)
COPY --from=builder /install /usr/local

# Copy application source preserving the directory structure
COPY src/ ./src/

# Copy YAML config files
COPY config/ ./config/

# Create data directory for runtime volumes
RUN mkdir -p /project/data && chown nobody:nogroup /project/data

# Run as non-root
USER nobody

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

# Use --app-dir so uvicorn can find the src package without it being pip-installed
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info", "--app-dir", "/project/src"]
