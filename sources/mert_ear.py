"""MERT-based music similarity engine — replaces Gemini audio analysis.

Downloads short audio clips via yt-dlp, embeds them with MERT-v1-95M,
and scores candidates by cosine similarity to the SOTD library.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torchaudio
from transformers import AutoModel, Wav2Vec2FeatureExtractor

_DATA_DIR = Path(os.environ.get("GROOVY_DATA_DIR", Path(__file__).parent.parent))
AUDIO_CACHE_DIR = _DATA_DIR / "audio_cache"
LIBRARY_INDEX_PATH = _DATA_DIR / "library_index.npz"
MODEL_NAME = "m-a-p/MERT-v1-95M"
SAMPLE_RATE = 24000
CLIP_DURATION_SEC = 60

# Lazy-loaded globals
_model = None
_processor = None


def _load_model():
    global _model, _processor
    if _model is None:
        print("[MERT] Loading model (first time may download ~380MB)...")
        _processor = Wav2Vec2FeatureExtractor.from_pretrained(MODEL_NAME, trust_remote_code=True)
        _model = AutoModel.from_pretrained(MODEL_NAME, trust_remote_code=True)
        _model.eval()
        print("[MERT] Model loaded.")
    return _model, _processor


def download_audio(yt_video_id: str, duration: int = CLIP_DURATION_SEC) -> Optional[Path]:
    """Download a short audio clip from YouTube using yt-dlp.

    Downloads the first `duration` seconds as a WAV file.
    Returns the path to the cached file, or None on failure.
    """
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    out_path = AUDIO_CACHE_DIR / f"{yt_video_id}.wav"

    if out_path.exists():
        return out_path

    url = f"https://www.youtube.com/watch?v={yt_video_id}"
    try:
        subprocess.run(
            [
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "wav",
                "--postprocessor-args", f"ffmpeg:-ss 30 -t {duration} -ac 1 -ar {SAMPLE_RATE}",
                "-o", str(AUDIO_CACHE_DIR / f"{yt_video_id}.%(ext)s"),
                "--no-playlist",
                "--quiet",
                url,
            ],
            check=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  [yt-dlp] Failed for {yt_video_id}: {e}")
        return None

    return out_path if out_path.exists() else None


def embed_audio(audio_path: Path) -> Optional[np.ndarray]:
    """Run MERT on an audio file and return a 768-dim embedding vector."""
    model, processor = _load_model()

    waveform, sr = torchaudio.load(str(audio_path))
    # Resample if needed
    if sr != SAMPLE_RATE:
        waveform = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(waveform)
    # Mono
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    waveform = waveform.squeeze(0)

    inputs = processor(waveform.numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs, output_hidden_states=True)

    # Stack all hidden states, mean over time → one vector per layer
    # Use the last hidden state (empirically strong for classification tasks)
    last_hidden = outputs.hidden_states[-1].squeeze(0)  # [time_steps, 768]
    embedding = last_hidden.mean(dim=0).numpy()  # [768]
    return embedding


def download_preview(preview_url: str, track_id: str) -> Optional[Path]:
    """Download a Spotify 30-second preview MP3 and convert to WAV.

    Returns path to the cached WAV file, or None on failure.
    """
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    out_path = AUDIO_CACHE_DIR / f"sp_{track_id}.wav"

    if out_path.exists():
        return out_path

    if not preview_url:
        return None

    try:
        import requests
        resp = requests.get(preview_url, timeout=15)
        resp.raise_for_status()

        # Save MP3 then convert to WAV with ffmpeg
        mp3_path = AUDIO_CACHE_DIR / f"sp_{track_id}.mp3"
        mp3_path.write_bytes(resp.content)

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path),
             "-ac", "1", "-ar", str(SAMPLE_RATE), str(out_path)],
            capture_output=True, timeout=30,
        )
        mp3_path.unlink(missing_ok=True)

        return out_path if out_path.exists() else None
    except Exception as e:
        print(f"  [Preview] Failed for {track_id}: {e}")
        return None


def embed_song(yt_video_id: str = None, preview_url: str = None, spotify_id: str = None) -> Optional[np.ndarray]:
    """Download + embed a single song. Tries Spotify preview first, falls back to yt-dlp.

    Returns 768-dim vector or None.
    """
    audio_path = None

    # Try Spotify preview first (works on servers, no bot detection)
    if preview_url and spotify_id:
        audio_path = download_preview(preview_url, spotify_id)

    # Fall back to yt-dlp (works locally, blocked on datacenter IPs)
    if audio_path is None and yt_video_id:
        audio_path = download_audio(yt_video_id)

    if audio_path is None:
        return None
    return embed_audio(audio_path)


# ── Library index ───────────────────────────────────────────────────

def build_library_index(library_tracks: list[dict], force: bool = False, on_progress=None) -> dict:
    """Build MERT embeddings for the entire SOTD library.

    Args:
        library_tracks: list of dicts with 'name', 'artist', 'youtube_link' keys
        force: rebuild even if index exists
        on_progress: optional callback(done, total, song_name) called after each song

    Returns:
        dict with 'embeddings' (N x 768 array) and 'songs' (list of song identifiers)
    """
    if LIBRARY_INDEX_PATH.exists() and not force:
        print(f"[MERT] Loading cached library index ({LIBRARY_INDEX_PATH})")
        return _load_index()

    print(f"[MERT] Building library index from {len(library_tracks)} tracks...")
    embeddings = []
    songs = []

    for i, track in enumerate(library_tracks):
        name = track.get("name", "?")
        artist = track.get("artist", "?")

        yt_link = track.get("youtube_link", "")
        vid = _extract_video_id(yt_link) or track.get("yt_video_id", "")
        preview_url = track.get("preview_url", "")
        spotify_id = track.get("spotify_id", "")

        if not vid and not preview_url:
            continue

        print(f"  [{i+1}/{len(library_tracks)}] {name} — {artist}")

        emb = embed_song(yt_video_id=vid, preview_url=preview_url, spotify_id=spotify_id)
        if emb is not None:
            embeddings.append(emb)
            songs.append({"name": name, "artist": artist, "yt_video_id": vid or ""})

        if on_progress:
            on_progress(len(embeddings), len(library_tracks), f"{name} — {artist}")

    if not embeddings:
        print("[MERT] No embeddings produced. Check YouTube links in your library CSV.")
        return {"embeddings": np.array([]), "songs": []}

    emb_array = np.stack(embeddings)
    # Save to disk
    np.savez(
        LIBRARY_INDEX_PATH,
        embeddings=emb_array,
        songs=json.dumps(songs),
    )
    print(f"[MERT] Library index saved: {len(songs)} songs, shape {emb_array.shape}")
    return {"embeddings": emb_array, "songs": songs}


def _load_index() -> dict:
    data = np.load(LIBRARY_INDEX_PATH, allow_pickle=True)
    return {
        "embeddings": data["embeddings"],
        "songs": json.loads(str(data["songs"])),
    }


def _extract_video_id(link: str) -> Optional[str]:
    if "watch?v=" in link:
        return link.split("watch?v=")[-1].split("&")[0]
    if "youtu.be/" in link:
        return link.split("youtu.be/")[-1].split("?")[0]
    return None


# ── Similarity scoring ──────────────────────────────────────────────

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def score_candidates(candidates: list[dict], library_index: dict) -> list[dict]:
    """Score each candidate by acoustic similarity to the SOTD library.

    Each candidate gets:
        - similarity_avg: mean similarity to all library songs
        - similarity_max: max similarity to any single library song
        - similarity_top5: mean of top 5 most similar library songs
        - closest_songs: the 3 most similar library songs

    Candidates are returned sorted by similarity_top5 (descending).
    """
    lib_embeddings = library_index["embeddings"]
    lib_songs = library_index["songs"]

    if len(lib_embeddings) == 0:
        print("[MERT] Empty library index — can't score.")
        return candidates

    scored = []
    for candidate in candidates:
        vid = candidate.get("yt_video_id", "")
        if not vid:
            continue

        name = candidate.get("name", "?")
        artist = candidate.get("artist", "?")
        print(f"  [MERT] Scoring: {name} — {artist}")

        emb = embed_song(vid)
        if emb is None:
            print(f"    → Failed to embed, skipping")
            continue

        # Compute similarity to every library song
        sims = np.array([cosine_similarity(emb, lib_emb) for lib_emb in lib_embeddings])

        top_indices = np.argsort(sims)[::-1]
        top5_sim = float(sims[top_indices[:5]].mean()) if len(sims) >= 5 else float(sims.mean())

        closest = []
        for idx in top_indices[:3]:
            closest.append({
                "name": lib_songs[idx]["name"],
                "artist": lib_songs[idx]["artist"],
                "similarity": round(float(sims[idx]), 4),
            })

        candidate["mert"] = {
            "similarity_avg": round(float(sims.mean()), 4),
            "similarity_max": round(float(sims.max()), 4),
            "similarity_top5": round(top5_sim, 4),
            "closest_songs": closest,
        }
        print(f"    → Top5 sim: {top5_sim:.3f} | Closest: {closest[0]['name']} ({closest[0]['similarity']:.3f})")
        scored.append(candidate)

    scored.sort(key=lambda s: s.get("mert", {}).get("similarity_top5", 0), reverse=True)
    return scored


# ── High-level API ──────────────────────────────────────────────────

def analyze_batch(candidates: list[dict], library_tracks: list[dict],
                  max_songs: int = 15) -> list[dict]:
    """Analyze a batch of candidates against the library using MERT similarity."""
    # Pre-filter non-songs
    skip_words = ["instrumental", "backing track", "karaoke", "compilation",
                  "mix", "playlist", "8d audio", "slowed", "reverb"]
    filtered = []
    for s in candidates[:max_songs]:
        title = (s.get("name", "") + " " + s.get("artist", "")).lower()
        if any(w in title for w in skip_words):
            print(f"  [Skip] {s.get('name', '?')} — {s.get('artist', '?')} (non-song)")
            continue
        filtered.append(s)

    # Build or load library index
    index = build_library_index(library_tracks)

    # Score candidates
    scored = score_candidates(filtered, index)
    return scored


def curate_top_picks(scored_songs: list[dict], top_n: int = 5) -> list[dict]:
    """Pick the top N diverse candidates from MERT-scored songs.

    Prioritizes: high similarity score, artist diversity.
    """
    picks = []
    seen_artists = set()

    for s in scored_songs:
        artist = s.get("artist", "").lower()
        if artist in seen_artists:
            continue

        picks.append(s)
        seen_artists.add(artist)

        if len(picks) >= top_n:
            break

    # Backfill if not enough diverse picks
    if len(picks) < top_n:
        for s in scored_songs:
            if s not in picks:
                picks.append(s)
            if len(picks) >= top_n:
                break

    return picks
