FROM python:3.11-slim

# spylls is pure Python — no system Hunspell library needed.
# The he_IL dictionary files are bundled in backend/dictionaries/.

WORKDIR /app

# Install Python dependencies first (layer-cached until requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and bundled dictionaries
COPY app/         ./app/
COPY dictionaries/ ./dictionaries/

# Create logs directory and a non-root user
RUN mkdir -p /app/logs && \
    useradd -m appuser && \
    chown -R appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
