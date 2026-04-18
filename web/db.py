"""SQLite database for Groovy."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("GROOVY_DATA_DIR", Path(__file__).resolve().parent.parent)) / "sotd.db"


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
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                picture TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
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
                user_id TEXT NOT NULL REFERENCES users(id),
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
                drop_date TEXT DEFAULT '',
                drop_order INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Migrations for existing databases
        for col, default in [("drop_date", "''"), ("drop_order", "0")]:
            try:
                conn.execute(f"ALTER TABLE songs ADD COLUMN {col} TEXT DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # column already exists


# ── Users ──────────────────────────────────────────────────────────


def upsert_user(user_id: str, email: str, name: str = "", picture: str = "") -> dict:
    """Create or update a user from Google OAuth info. Returns the user dict."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO users (id, email, name, picture)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   email = excluded.email,
                   name = excluded.name,
                   picture = excluded.picture""",
            (user_id, email, name, picture),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row)


def get_user(user_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


# ── Playlists ──────────────────────────────────────────────────────


def add_playlist(user_id: str, playlist_id: str, url: str, platform: str, title: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO playlists (id, user_id, url, platform, title) VALUES (?, ?, ?, ?, ?)",
            (playlist_id, user_id, url, platform, title),
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


def get_playlists(user_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.url, p.platform, p.title,
                   COUNT(pt.id) AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            WHERE p.user_id = ?
            GROUP BY p.id
            ORDER BY p.created_at
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_playlist(playlist_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return dict(row) if row else None


def remove_playlist(user_id: str, playlist_id: str):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM playlists WHERE id = ? AND user_id = ?",
            (playlist_id, user_id),
        )


def get_all_tracks(user_id: str) -> list[dict]:
    """Get all tracks across a user's playlists, deduped by (name, artist)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT pt.name, pt.artist, pt.album, pt.yt_video_id, pt.spotify_link, pt.genres
            FROM playlist_tracks pt
            JOIN playlists p ON p.id = pt.playlist_id
            WHERE p.user_id = ?
            GROUP BY LOWER(TRIM(pt.name)), LOWER(TRIM(pt.artist))
            ORDER BY pt.id
        """, (user_id,)).fetchall()
        tracks = []
        for r in rows:
            t = dict(r)
            t["genres"] = json.loads(t["genres"])
            if t["yt_video_id"]:
                t["youtube_link"] = f"https://www.youtube.com/watch?v={t['yt_video_id']}"
                t["yt_link"] = t["youtube_link"]
            tracks.append(t)
        return tracks


# ── Songs (discovered) ─────────────────────────────────────────────


def save_song(user_id: str, song: dict):
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO songs
               (id, user_id, name, artist, album, yt_video_id, yt_link, spotify_link,
                view_count, release_year, source_query, source_strategy, mert_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                song["_id"],
                user_id,
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


def get_song(user_id: str, song_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM songs WHERE id = ? AND user_id = ?",
            (song_id, user_id),
        ).fetchone()
        if not row:
            return None
        s = dict(row)
        s["_id"] = s.pop("id")
        s["mert"] = json.loads(s.pop("mert_data"))
        return s


def update_song_status(user_id: str, song_id: str, status: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE songs SET status = ? WHERE id = ? AND user_id = ?",
            (status, song_id, user_id),
        )


def update_song_rating(user_id: str, song_id: str, rating: int):
    with get_db() as conn:
        status = "approved" if rating >= 4 else "discovered"
        conn.execute(
            "UPDATE songs SET rating = ?, status = ? WHERE id = ? AND user_id = ?",
            (rating, status, song_id, user_id),
        )


def get_approved_tracks(user_id: str) -> list[dict]:
    """Get all approved/highly-rated songs as track dicts (for taste feedback loop)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM songs WHERE user_id = ? AND status = 'approved' ORDER BY created_at",
            (user_id,),
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


# ── Drops ──────────────────────────────────────────────────────────


def _song_row_to_dict(row) -> dict:
    """Convert a songs table row to the dict format the frontend expects."""
    s = dict(row)
    s["_id"] = s.pop("id")
    s["mert"] = json.loads(s.pop("mert_data"))
    return s


def tag_drop(user_id: str, song_ids: list[str], drop_date: str):
    """Mark songs as part of a daily drop."""
    with get_db() as conn:
        for i, song_id in enumerate(song_ids):
            conn.execute(
                "UPDATE songs SET drop_date = ?, drop_order = ? WHERE id = ? AND user_id = ?",
                (drop_date, i, song_id, user_id),
            )


def get_drop(user_id: str, drop_date: str) -> list[dict]:
    """Get all songs for a specific daily drop, ordered by drop_order."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM songs
               WHERE user_id = ? AND drop_date = ?
               ORDER BY drop_order""",
            (user_id, drop_date),
        ).fetchall()
        return [_song_row_to_dict(r) for r in rows]


def get_collection(user_id: str) -> list[dict]:
    """Get all liked songs (rating >= 4) across all drops."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM songs
               WHERE user_id = ? AND status = 'approved'
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [_song_row_to_dict(r) for r in rows]


def get_drop_dates(user_id: str) -> list[dict]:
    """Get all drop dates with stats, most recent first."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT drop_date,
                      COUNT(*) as total,
                      SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as liked,
                      SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) as skipped
               FROM songs
               WHERE user_id = ? AND drop_date != ''
               GROUP BY drop_date
               ORDER BY drop_date DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
