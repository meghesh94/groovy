FROM python:3.11-slim

# System deps: ffmpeg for yt-dlp audio extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Persistent data (SQLite, audio cache, MERT index)
# HF Spaces runs as uid 1000 — ensure /data is writable
ENV GROOVY_DATA_DIR=/data
RUN mkdir -p /data/audio_cache && chmod -R 777 /data

# HF Spaces exposes port 7860
ENV PORT=7860
EXPOSE 7860

# Run as non-root (HF Spaces requirement)
RUN useradd -m -u 1000 appuser
USER appuser

CMD ["python", "web/app.py"]
