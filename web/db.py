"""SQLite database for SOTD Music Agent."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "sotd.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT DEFAULT '',
                yt_video_id TEXT DEFAULT '',
                spotify_link TEXT DEFAULT '',
                genres TEXT DEFAULT '[]',
                UNIQUE(playlist_id, name, artist)
            );

            CREATE TABLE IF NOT EXISTS songs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT DEFAULT '',
                yt_video_id TEXT DEFAULT '',
                yt_link TEXT DEFAULT '',
                spotify_link TEXT DEFAULT '',
                view_count INTEGER DEFAULT 0,
                release_year INTEGER DEFAULT 0,
                source_query TEXT DEFAULT '',
                source_strategy TEXT DEFAULT '',
                mert_data TEXT DEFAULT '{}',
                rating INTEGER DEFAULT 0,
                status TEXT DEFAULT 'discovered',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)


# ── Playlists ──────────────────────────────────────────────────────


def add_playlist(playlist_id: str, url: str, platform: str, title: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO playlists (id, url, platform, title) VALUES (?, ?, ?, ?)",
            (playlist_id, url, platform, title),
        )


def add_playlist_tracks(playlist_id: str, tracks: list[dict]):
    with get_db() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO playlist_tracks
               (playlist_id, name, artist, album, yt_video_id, spotify_link, genres)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    playlist_id,
                    t.get("name", ""),
                    t.get("artist", ""),
                    t.get("album", ""),
                    t.get("yt_video_id", ""),
                    t.get("spotify_link", ""),
                    json.dumps(t.get("genres", [])),
                )
                for t in tracks
            ],
        )


def get_playlists() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.url, p.platform, p.title,
                   COUNT(pt.id) AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at
        """).fetchall()
        return [dict(r) for r in rows]


def get_playlist(playlist_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return dict(row) if row else None


def remove_playlist(playlist_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))


def get_all_tracks() -> list[dict]:
    """Get all tracks across all playlists, deduped by (name, artist)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT name, artist, album, yt_video_id, spotify_link, genres
            FROM playlist_tracks
            GROUP BY LOWER(TRIM(name)), LOWER(TRIM(artist))
            ORDER BY id
        """).fetchall()
        tracks = []
        for r in rows:
            t = dict(r)
            t["genres"] = json.loads(t["genres"])
            # Ensure youtube_link is set for MERT indexing
            if t["yt_video_id"]:
                t["youtube_link"] = f"https://www.youtube.com/watch?v={t['yt_video_id']}"
                t["yt_link"] = t["youtube_link"]
            tracks.append(t)
        return tracks


# ── Songs (discovered) ─────────────────────────────────────────────


def save_song(song: dict):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO songs
               (id, name, artist, album, yt_video_id, yt_link, spotify_link,
                view_count, release_year, source_query, source_strategy, mert_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                song["_id"],
                song.get("name", ""),
                song.get("artist", ""),
                song.get("album", ""),
                song.get("yt_video_id", ""),
                song.get("yt_link", ""),
                song.get("spotify_link", ""),
                song.get("view_count") or 0,
                song.get("release_year") or 0,
                song.get("source_query", ""),
                song.get("source_strategy", ""),
                json.dumps(song.get("mert", {})),
            ),
        )


def get_song(song_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        if not row:
            return None
        s = dict(row)
        s["_id"] = s.pop("id")
        s["mert"] = json.loads(s.pop("mert_data"))
        return s


def update_song_status(song_id: str, status: str):
    with get_db() as conn:
        conn.execute("UPDATE songs SET status = ? WHERE id = ?", (status, song_id))


def update_song_rating(song_id: str, rating: int):
    with get_db() as conn:
        status = "approved" if rating >= 4 else "discovered"
        conn.execute(
            "UPDATE songs SET rating = ?, status = ? WHERE id = ?",
            (rating, status, song_id),
        )


def get_approved_tracks() -> list[dict]:
    """Get all approved/highly-rated songs as track dicts (for taste feedback loop)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM songs WHERE status = 'approved' ORDER BY created_at"
        ).fetchall()
        tracks = []
        for r in rows:
            s = dict(r)
            tracks.append({
                "name": s["name"],
                "artist": s["artist"],
                "album": s["album"],
                "yt_video_id": s["yt_video_id"],
                "spotify_link": s["spotify_link"],
                "genres": [],
            })
        return tracks
