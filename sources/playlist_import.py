"""Import tracks from Spotify and YT Music playlist URLs."""

import re
from typing import Optional


def parse_playlist_url(url: str) -> Optional[dict]:
    """Parse a playlist URL and return {platform, playlist_id} or None."""
    url = url.strip()

    # YT Music: https://music.youtube.com/playlist?list=PLxxxxxx
    m = re.search(r"music\.youtube\.com/playlist\?list=([A-Za-z0-9_-]+)", url)
    if m:
        return {"platform": "ytmusic", "playlist_id": m.group(1)}

    # Regular YouTube playlist
    m = re.search(r"youtube\.com/playlist\?list=([A-Za-z0-9_-]+)", url)
    if m:
        return {"platform": "ytmusic", "playlist_id": m.group(1)}

    # Spotify: https://open.spotify.com/playlist/xxxxxxxx
    m = re.search(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)", url)
    if m:
        return {"platform": "spotify", "playlist_id": m.group(1)}

    return None


def fetch_ytmusic_playlist(playlist_id: str) -> dict:
    """Fetch all tracks from a YT Music playlist using ytmusicapi (no yt-dlp).

    Returns {title, track_count, tracks: [{name, artist, album, yt_video_id, yt_link}]}
    """
    from ytmusicapi import YTMusic
    yt = YTMusic()

    try:
        pl = yt.get_playlist(playlist_id, limit=500)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch YT Music playlist: {e}")

    playlist_title = pl.get("title", "YouTube Playlist")
    tracks = []

    for item in pl.get("tracks", []):
        vid = item.get("videoId", "")
        title = item.get("title", "")
        artists = item.get("artists", [])
        artist = ", ".join(a.get("name", "") for a in artists) if artists else ""
        album_info = item.get("album")
        album = album_info.get("name", "") if album_info else ""

        if vid and title:
            tracks.append({
                "name": title,
                "artist": artist,
                "album": album,
                "yt_video_id": vid,
                "yt_link": f"https://music.youtube.com/watch?v={vid}",
                "youtube_link": f"https://www.youtube.com/watch?v={vid}",
            })

    # Resolve Spotify preview URLs for MERT scoring on servers
    try:
        from sources.spotify import resolve_preview_urls
        tracks = resolve_preview_urls(tracks)
    except Exception as e:
        print(f"[Playlist] Spotify preview resolution failed (non-fatal): {e}")

    return {
        "title": playlist_title,
        "track_count": len(tracks),
        "tracks": tracks,
    }


def fetch_spotify_playlist(playlist_id: str) -> dict:
    """Fetch all tracks from a Spotify playlist with preview URLs."""
    from sources.spotify import get_playlist_tracks, _get_public_client

    sp = _get_public_client()
    pl_info = sp.playlist(playlist_id, fields="name")
    tracks = get_playlist_tracks(playlist_id)

    # Also resolve YT video IDs for playback in the UI
    tracks = _resolve_yt_ids(tracks)

    return {
        "title": pl_info.get("name", "Unknown Playlist"),
        "track_count": len(tracks),
        "tracks": tracks,
    }


def _resolve_yt_ids(tracks: list[dict]) -> list[dict]:
    """Search YT Music to find video IDs for Spotify tracks."""
    from ytmusicapi import YTMusic
    yt = YTMusic()

    for t in tracks:
        query = f"{t['name']} {t['artist']}"
        try:
            results = yt.search(query, filter="songs", limit=1)
            if results:
                t["yt_video_id"] = results[0].get("videoId", "")
                t["yt_link"] = f"https://music.youtube.com/watch?v={t['yt_video_id']}" if t["yt_video_id"] else ""
                t["youtube_link"] = f"https://www.youtube.com/watch?v={t['yt_video_id']}" if t["yt_video_id"] else ""
        except Exception:
            t["yt_video_id"] = ""
            t["yt_link"] = ""
            t["youtube_link"] = ""

    return tracks


def fetch_playlist(url: str) -> dict:
    """Fetch tracks from any supported playlist URL.

    Returns {platform, playlist_id, title, track_count, tracks: [...]}
    """
    parsed = parse_playlist_url(url)
    if not parsed:
        raise ValueError(f"Unrecognized playlist URL: {url}")

    if parsed["platform"] == "ytmusic":
        result = fetch_ytmusic_playlist(parsed["playlist_id"])
    elif parsed["platform"] == "spotify":
        result = fetch_spotify_playlist(parsed["playlist_id"])
    else:
        raise ValueError(f"Unsupported platform: {parsed['platform']}")

    result["platform"] = parsed["platform"]
    result["playlist_id"] = parsed["playlist_id"]
    result["url"] = url
    return result
