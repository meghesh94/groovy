FROM python:3.11-slim

# System deps: ffmpeg for yt-dlp audio extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download MERT model into the image (~380MB)
# This avoids runtime download which can fail on free tier
ENV HF_HOME=/app/.hf_cache
RUN python -c "from transformers import AutoModel, Wav2Vec2FeatureExtractor; \
    Wav2Vec2FeatureExtractor.from_pretrained('m-a-p/MERT-v1-95M', trust_remote_code=True); \
    AutoModel.from_pretrained('m-a-p/MERT-v1-95M', trust_remote_code=True); \
    print('MERT model cached')"

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
