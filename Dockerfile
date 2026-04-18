FROM python:3.11-slim

# System deps: ffmpeg for yt-dlp audio extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user early so model cache is owned by them
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download MERT model as appuser so cache is readable at runtime
ENV HF_HOME=/home/appuser/.cache/huggingface
USER appuser
RUN python -c "\
from transformers import AutoModel, Wav2Vec2FeatureExtractor; \
print('Downloading MERT model...'); \
Wav2Vec2FeatureExtractor.from_pretrained('m-a-p/MERT-v1-95M', trust_remote_code=True); \
AutoModel.from_pretrained('m-a-p/MERT-v1-95M', trust_remote_code=True); \
print('MERT model cached successfully')"

# Switch back to root for file operations
USER root

# Copy app code
COPY . .
RUN chown -R appuser:appuser /app

# Persistent data (SQLite, audio cache, MERT index)
ENV GROOVY_DATA_DIR=/data
RUN mkdir -p /data/audio_cache && chown -R appuser:appuser /data

# HF Spaces exposes port 7860
ENV PORT=7860
EXPOSE 7860

USER appuser
CMD ["python", "web/app.py"]
