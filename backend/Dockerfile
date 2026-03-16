FROM python:3.11-slim AS builder

# Build-time tools needed to compile the hunspell C extension
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential pkg-config libhunspell-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Runtime only: libhunspell shared library + curl for dictionary download
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates libhunspell-1.7-0 && \
    rm -rf /var/lib/apt/lists/*

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code
COPY app/ ./app/

# ── Hebrew dictionary ──────────────────────────────────────────────────────────
# Copy bundled dictionary as fallback, then try to download the latest from
# wooorm/dictionaries which tracks LibreOffice Hebrew dictionary releases.
COPY dictionaries/ ./dictionaries/

RUN echo "Downloading latest he_IL dictionary from wooorm/dictionaries..." && \
    curl -fsSL --retry 3 --retry-delay 2 --max-time 30 \
      "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/he/index.aff" \
      -o /app/dictionaries/he_IL.aff && \
    curl -fsSL --retry 3 --retry-delay 2 --max-time 30 \
      "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/he/index.dic" \
      -o /app/dictionaries/he_IL.dic && \
    echo "Downloaded latest dictionary: $(wc -l < /app/dictionaries/he_IL.dic) words" || \
    echo "Download failed — using bundled dictionary ($(wc -l < /app/dictionaries/he_IL.dic) words)"

# English dictionary (en_US) — downloaded at build time, no bundled fallback
RUN echo "Downloading en_US dictionary..." && \
    curl -fsSL --retry 3 --retry-delay 2 --max-time 30 \
      "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/en/index.aff" \
      -o /app/dictionaries/en_US.aff && \
    curl -fsSL --retry 3 --retry-delay 2 --max-time 30 \
      "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/en/index.dic" \
      -o /app/dictionaries/en_US.dic && \
    echo "en_US ready: $(wc -l < /app/dictionaries/en_US.dic) words" || \
    echo "en_US download failed — English spell-check will be unavailable"

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
