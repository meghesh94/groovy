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

# Persistent data (SQLite, audio cache, MERT index) lives here
# On HF Spaces, mount a persistent volume at /data
ENV GROOVY_DATA_DIR=/data
RUN mkdir -p /data/audio_cache

# HF Spaces exposes port 7860
ENV PORT=7860
EXPOSE 7860

CMD ["python", "web/app.py"]
