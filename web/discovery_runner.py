"""Background discovery runner with event streaming."""

import random
import threading
import uuid
from dataclasses import dataclass, field
from queue import Queue
from typing import Optional

import numpy as np


def _format_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


@dataclass
class RunConfig:
    radio_seeds_count: int = 8
    vibe_queries_count: int = 8
    artist_vibe_count: int = 5
    era_queries_count: int = 4
    listen_count: int = 15
    final_picks: int = 5
    popularity_min: int = 0
    popularity_max: int = 5_000_000  # 5M views
    year_min: int = 0
    year_max: int = 2026
    disabled_queries: list = field(default_factory=list)
    skip_words: list = field(default_factory=lambda: [
        "instrumental", "backing track", "karaoke", "compilation",
        "mix", "playlist", "8d audio", "slowed", "reverb",
    ])


# Global state
_current_run_id: Optional[str] = None
_run_lock = threading.Lock()
_event_queue: Queue = Queue()


def is_running() -> bool:
    return _current_run_id is not None


def force_reset():
    """Force-clear the run state (e.g. after a stuck run)."""
    global _current_run_id
    with _run_lock:
        _current_run_id = None
    while not _event_queue.empty():
        try:
            _event_queue.get_nowait()
        except Exception:
            break


def get_event_queue() -> Queue:
    return _event_queue


def _emit(event_type: str, **data):
    _event_queue.put({"type": event_type, **data})


MOOD_TEMPLATES = [
    "songs that feel like a road trip",
    "songs that feel like a warm hug",
    "songs with beautiful build up",
    "melancholic songs late night",
    "songs that give you chills",
    "beautiful acoustic songs undiscovered",
    "songs that sound like a movie soundtrack",
    "bittersweet love songs",
    "songs that make you feel nostalgic",
    "chill songs with deep lyrics",
    "songs that feel like coming home",
    "emotional storytelling songs",
    "soulful songs with raw vocals",
    "atmospheric dreamy songs",
    "anthemic songs that make you want to drive",
    "intimate acoustic songs",
    "songs with a crescendo ending",
    "hidden gem songs under 1 million views",
    "songs that hit you in the first 10 seconds",
    "songs you'd play at a bonfire",
]

ERA_TEMPLATES = [
    "best undiscovered songs 2025 2026",
    "new indie releases 2026",
    "best new music 2026",
    "fresh discoveries this year",
    "new artists 2025 2026 underrated",
    "best songs released this month",
]


def _generate_queries_dynamic(profile, config: RunConfig):
    """Generate search queries dynamically from the user's actual library.

    Radio seeds: random songs from their playlists → YT Music "Up Next"
    Artist exploration: "artists similar to X" from their top artists
    Mood discovery: generic mood templates (not artist-specific)
    New releases: era-based searches
    """
    tracks = profile.get("tracks", [])
    top_artists = profile.get("top_artists", [])
    disabled = set(config.disabled_queries)
    queries = []

    # Year suffix for queries — forces YT Music to prioritize recent results
    year_tag = ""
    if config.year_min >= 2020:
        year_tag = f" {config.year_min} {config.year_max}"

    # 1. Radio seeds — pick random songs from the user's actual library
    seeds_with_ids = [t for t in tracks if t.get("yt_video_id")]
    available = [t for t in seeds_with_ids if f"{t.get('name', '')} {t.get('artist', '')}" not in disabled]
    for t in random.sample(available, min(config.radio_seeds_count, len(available))):
        queries.append({
            "query": f"{t['name']} {t['artist']}",
            "source": "ytmusic_radio",
            "strategy": "radio_seed",
        })

    # 2. Artist exploration — "artists similar to X" + year hint
    artist_names = [a["name"] for a in top_artists if a["name"]]
    if not artist_names:
        seen = set()
        for t in tracks:
            a = t.get("artist", "").strip()
            if a and a not in seen:
                artist_names.append(a)
                seen.add(a)
    available = [a for a in artist_names if f"artists similar to {a}" not in disabled]
    for artist in random.sample(available, min(config.artist_vibe_count, len(available))):
        queries.append({
            "query": f"artists similar to {artist} new songs{year_tag}",
            "source": "ytmusic",
            "strategy": "artist_vibe",
        })

    # 3. "Songs like X by Y" + year hint
    tracks_with_names = [t for t in tracks if t.get("name") and t.get("artist")]
    for t in random.sample(tracks_with_names, min(config.era_queries_count, len(tracks_with_names))):
        queries.append({
            "query": f"songs like {t['name']} by {t['artist']}{year_tag}",
            "source": "ytmusic",
            "strategy": "genre_era",
        })

    random.shuffle(queries)
    return queries


def _run_discovery(config: RunConfig, library_tracks=None):
    """Execute the full discovery pipeline, emitting events at each step."""
    global _current_run_id

    try:
        # Phase 1: Taste profile
        _emit("status", phase="profile", message="Building taste profile...")
        from collections import Counter

        if not library_tracks:
            _emit("error", message="No library tracks. Import playlists first.")
            return

        artist_counter = Counter()
        genre_counter = Counter()
        for t in library_tracks:
            if t.get("artist"):
                artist_counter[t["artist"]] += 1
            for g in t.get("genres", []):
                genre_counter[g] += 1
        profile = {
            "tracks": library_tracks,
            "track_count": len(library_tracks),
            "top_artists": [{"name": n, "count": c} for n, c in artist_counter.most_common(20)],
            "top_genres": [g for g, _ in genre_counter.most_common(20)],
        }
        _emit("profile", track_count=profile["track_count"],
              top_artists=[a for a in profile["top_artists"][:15]],
              top_genres=profile["top_genres"][:15])

        # Phase 2: Generate queries
        _emit("status", phase="queries", message="Generating search queries...")
        queries = _generate_queries_dynamic(profile, config)
        _emit("queries", count=len(queries),
              breakdown={
                  "radio_seed": sum(1 for q in queries if q["strategy"] == "radio_seed"),
                  "vibe_search": sum(1 for q in queries if q["strategy"] == "vibe_search"),
                  "artist_vibe": sum(1 for q in queries if q["strategy"] == "artist_vibe"),
                  "genre_era": sum(1 for q in queries if q["strategy"] == "genre_era"),
              })

        # Phase 3: Search
        _emit("status", phase="searching", message="Searching YT Music...")
        raw = []
        for i, q in enumerate(queries):
            _emit("progress", phase="searching", done=i + 1, total=len(queries),
                  query=q["query"][:50])
            try:
                from sources import ytmusic
                if q["source"] == "ytmusic_radio":
                    yt_results = ytmusic.search_songs(q["query"], limit=1)
                    if yt_results and yt_results[0].get("yt_video_id"):
                        radio = ytmusic.get_watch_playlist(yt_results[0]["yt_video_id"])
                        for s in radio:
                            s["_source_query"] = q["query"]
                            s["_source_strategy"] = q["strategy"]
                        raw += radio
                else:
                    results = ytmusic.search_songs(q["query"], limit=20)
                    for s in results:
                        s["_source_query"] = q["query"]
                        s["_source_strategy"] = q["strategy"]
                    raw += results
            except Exception as e:
                _emit("warning", message=f"Query failed: {q['query'][:30]}: {e}")

        # Phase 4: Deduplicate
        _emit("status", phase="dedup", message="Deduplicating...")
        library_keys = {(t["name"].lower().strip(), t["artist"].lower().strip()) for t in profile["tracks"]}
        seen = set(library_keys)
        unique = []
        for c in raw:
            key = (c.get("name", "").lower().strip(), c.get("artist", "").lower().strip())
            if key not in seen and key[0] and key[1]:
                seen.add(key)
                unique.append(c)
        _emit("dedup", raw_count=len(raw), unique_count=len(unique))

        if not unique:
            _emit("complete", picks=[], message="No new songs found.")
            return

        # Phase 4.5: View count + year filter via yt-dlp
        max_views = config.popularity_max
        min_views = config.popularity_min
        filter_views = max_views < 500_000_000
        filter_year = config.year_min > 0 or config.year_max < 2026

        if filter_views or filter_year:
            _emit("status", phase="filtering", message=f"Checking views & release year ({len(unique)} songs)...")
            import subprocess
            before_count = len(unique)
            filtered = []
            for i, song in enumerate(unique):
                vid = song.get("yt_video_id", "")
                name = song.get("name", "")
                artist = song.get("artist", "")
                if not vid:
                    filtered.append(song)
                    continue
                if i % 5 == 0:
                    _emit("progress", phase="filtering", done=i, total=len(unique),
                          song=f"{name} — {artist}")
                try:
                    result = subprocess.run(
                        ["yt-dlp", "--skip-download",
                         "--print", "%(view_count)s\t%(upload_date)s",
                         "--no-warnings",
                         f"https://www.youtube.com/watch?v={vid}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    parts = result.stdout.strip().split("\t")
                    views_str = parts[0] if parts else "0"
                    date_str = parts[1] if len(parts) > 1 else ""

                    views = int(views_str) if views_str.isdigit() else 0
                    year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else 0

                    song["view_count"] = views
                    song["release_year"] = year

                    # Apply filters
                    if filter_views and not (min_views <= views <= max_views):
                        continue
                    if filter_year and year > 0:
                        if year < config.year_min or year > config.year_max:
                            continue

                    filtered.append(song)
                except Exception:
                    song["view_count"] = 0
                    song["release_year"] = 0
                    filtered.append(song)

            msg_parts = []
            if filter_views:
                msg_parts.append(f"under {_format_views(max_views)} views")
            if filter_year:
                msg_parts.append(f"{config.year_min}–{config.year_max}")
            _emit("status", phase="filtering",
                  message=f"Filtered: {len(filtered)} of {before_count} songs ({', '.join(msg_parts)})")
            unique = filtered

        if not unique:
            _emit("complete", picks=[], message="All songs filtered out. Try widening the filters.")
            return

        # Phase 5: MERT scoring
        _emit("status", phase="mert", message="Loading MERT model...")
        from sources.mert_ear import build_library_index, embed_song, cosine_similarity, _load_index

        index = build_library_index(profile["tracks"])
        lib_embeddings = index["embeddings"]
        lib_songs = index["songs"]

        if len(lib_embeddings) == 0:
            _emit("complete", picks=[], message="Empty library index.")
            return

        # Filter non-songs
        skip_lower = [w.lower() for w in config.skip_words]
        sample = random.sample(unique, min(config.listen_count, len(unique)))
        filtered = []
        for s in sample:
            title = (s.get("name", "") + " " + s.get("artist", "")).lower()
            if not any(w in title for w in skip_lower):
                filtered.append(s)

        _emit("status", phase="scoring", message=f"Scoring {len(filtered)} songs with MERT...")
        scored = []
        for i, candidate in enumerate(filtered):
            vid = candidate.get("yt_video_id", "")
            if not vid:
                continue

            name = candidate.get("name", "?")
            artist = candidate.get("artist", "?")
            _emit("progress", phase="scoring", done=i + 1, total=len(filtered),
                  song=f"{name} — {artist}")

            emb = embed_song(vid)
            if emb is None:
                _emit("warning", message=f"Failed to embed: {name} — {artist}")
                continue

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
            candidate["_id"] = str(uuid.uuid4())[:8]
            scored.append(candidate)

            # Stream each result as it's scored
            _emit("result", song={
                "_id": candidate["_id"],
                "name": name,
                "artist": artist,
                "album": candidate.get("album", ""),
                "yt_video_id": vid,
                "yt_link": candidate.get("yt_link", ""),
                "spotify_link": candidate.get("spotify_link", ""),
                "view_count": candidate.get("view_count"),
                "release_year": candidate.get("release_year"),
                "source_query": candidate.get("_source_query", ""),
                "source_strategy": candidate.get("_source_strategy", ""),
                "mert": candidate["mert"],
            })

        # Phase 6: Curate
        _emit("status", phase="curating", message="Selecting top picks...")
        scored.sort(key=lambda s: s.get("mert", {}).get("similarity_top5", 0), reverse=True)

        picks = []
        seen_artists = set()
        for s in scored:
            artist = s.get("artist", "").lower()
            if artist in seen_artists:
                continue
            picks.append(s["_id"])
            seen_artists.add(artist)
            if len(picks) >= config.final_picks:
                break
        if len(picks) < config.final_picks:
            for s in scored:
                if s["_id"] not in picks:
                    picks.append(s["_id"])
                if len(picks) >= config.final_picks:
                    break

        _emit("complete", picks=picks, message=f"Done! {len(picks)} songs curated from {len(scored)} scored.")

    except Exception as e:
        _emit("error", message=str(e))
    finally:
        with _run_lock:
            _current_run_id = None


def start_discovery(config: RunConfig, library_tracks=None) -> str:
    """Start a discovery run in a background thread. Returns run_id or raises if busy."""
    global _current_run_id

    with _run_lock:
        if _current_run_id is not None:
            raise RuntimeError("A discovery run is already active.")
        _current_run_id = str(uuid.uuid4())[:8]
        run_id = _current_run_id

    # Clear any stale events
    while not _event_queue.empty():
        try:
            _event_queue.get_nowait()
        except Exception:
            break

    thread = threading.Thread(target=_run_discovery, args=(config, library_tracks), daemon=True)
    thread.start()
    return run_id
