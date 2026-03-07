FROM python:3.11-slim

# spylls is pure Python — no system Hunspell library needed.

WORKDIR /app

# Install curl (needed to download latest dictionary) and clean up in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer-cached until requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# ── Hebrew dictionary ──────────────────────────────────────────────────────────
# Copy bundled dictionary as fallback, then try to download the latest from
# wooorm/dictionaries which tracks LibreOffice Hebrew dictionary releases.
COPY dictionaries/ ./dictionaries/

RUN echo "Downloading latest he_IL dictionary from wooorm/dictionaries..." && \
    curl -fsSL --retry 3 --retry-delay 2 \
      "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/he/index.aff" \
      -o /app/dictionaries/he_IL.aff && \
    curl -fsSL --retry 3 --retry-delay 2 \
      "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/he/index.dic" \
      -o /app/dictionaries/he_IL.dic && \
    echo "Downloaded latest dictionary: $(wc -l < /app/dictionaries/he_IL.dic) words" || \
    echo "Download failed — using bundled dictionary ($(wc -l < /app/dictionaries/he_IL.dic) words)"

# Create logs directory and a non-root user
RUN mkdir -p /app/logs && \
    useradd -m appuser && \
    chown -R appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 2 workers give true CPU parallelism (separate processes bypass Python GIL)
# uvloop + httptools are the fastest async event loop and HTTP parser available
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--loop", "uvloop", \
     "--http", "httptools"]
