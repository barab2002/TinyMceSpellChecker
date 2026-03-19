FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential pkg-config libhunspell-dev curl ca-certificates libhunspell-1.7-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

RUN useradd -m appuser
USER appuser

EXPOSE 8000
CMD ["sleep", "infinity"]
