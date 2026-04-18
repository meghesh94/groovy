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
    """Fetch all tracks from a YT Music / YouTube playlist using yt-dlp.

    Uses yt-dlp for reliable extraction — handles private playlists, auth, API changes.
    Returns {title, track_count, tracks: [{name, artist, album, yt_video_id, yt_link}]}
    """
    import subprocess
    import json as _json

    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    try:
        result = subprocess.run(
            [
                "yt-dlp", "--flat-playlist",
                "--dump-json",
                "--no-warnings",
                url,
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise RuntimeError(f"yt-dlp failed: {e}")

    tracks = []
    playlist_title = "YouTube Playlist"
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            item = _json.loads(line)
        except _json.JSONDecodeError:
            continue

        vid = item.get("id", "")
        title = item.get("title", "")
        artist = item.get("channel", item.get("uploader", ""))

        # yt-dlp often gives "Artist - Song" in the title for music
        if " - " in title and not artist:
            parts = title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        if playlist_title == "YouTube Playlist" and item.get("playlist_title"):
            playlist_title = item["playlist_title"]

        if vid:
            tracks.append({
                "name": title,
                "artist": artist,
                "album": "",
                "yt_video_id": vid,
                "yt_link": f"https://music.youtube.com/watch?v={vid}",
                "youtube_link": f"https://www.youtube.com/watch?v={vid}",
            })

    if not tracks and result.stderr:
        raise RuntimeError(f"yt-dlp error: {result.stderr[:200]}")

    return {
        "title": playlist_title,
        "track_count": len(tracks),
        "tracks": tracks,
    }


def fetch_spotify_playlist(playlist_id: str) -> dict:
    """Fetch all tracks from a Spotify playlist.

    Returns {title, track_count, tracks: [{name, artist, album, spotify_link, ...}]}
    Note: Spotify tracks don't have YT video IDs — we search YT Music to find them.
    """
    from sources.spotify import _get_client
    sp = _get_client()

    pl_info = sp.playlist(playlist_id, fields="name")
    results = sp.playlist_tracks(playlist_id, limit=100)

    raw_tracks = []
    while True:
        for item in results["items"]:
            t = item.get("track")
            if not t:
                continue
            raw_tracks.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album": t["album"]["name"],
                "spotify_link": t["external_urls"].get("spotify", ""),
            })
        if results["next"]:
            results = sp.next(results)
        else:
            break

    # Resolve YT video IDs for MERT embedding
    tracks = _resolve_yt_ids(raw_tracks)

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
