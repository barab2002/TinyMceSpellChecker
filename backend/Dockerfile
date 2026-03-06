# ─── Stage 1: build/deps ─────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# Install system deps: Hunspell C library + Hebrew dictionary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libhunspell-dev \
    hunspell-he \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM base AS runtime

WORKDIR /app

# Copy application code
COPY app/ ./app/

# Copy dictionaries (he_IL.aff / he_IL.dic will come from hunspell-he package;
# we also copy custom dictionary seed)
COPY dictionaries/ ./dictionaries/

# Create symlinks to system Hunspell Hebrew dictionary if not already present
# hunspell-he installs to /usr/share/hunspell/
RUN if [ ! -f /app/dictionaries/he_IL.aff ]; then \
      ln -sf /usr/share/hunspell/he_IL.aff /app/dictionaries/he_IL.aff; \
    fi && \
    if [ ! -f /app/dictionaries/he_IL.dic ]; then \
      ln -sf /usr/share/hunspell/he_IL.dic /app/dictionaries/he_IL.dic; \
    fi

# Create logs directory
RUN mkdir -p /app/logs

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Copy .env if present (docker-compose overrides via env_file anyway)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
