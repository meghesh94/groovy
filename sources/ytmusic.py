"""YT Music search and playlist management using ytmusicapi."""

import os

from ytmusicapi import YTMusic
from ytmusicapi.auth.oauth import OAuthCredentials

import config


def _get_public_client():
    """Unauthenticated client — for search and radio (no login needed)."""
    return YTMusic()


def _get_auth_client():
    """Authenticated client — for playlist modifications only."""
    client_id = os.environ.get("YTMUSIC_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("YTMUSIC_OAUTH_CLIENT_SECRET", "")
    if client_id and client_secret:
        creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
        return YTMusic(config.YTMUSIC_AUTH_FILE, oauth_credentials=creds)
    return YTMusic(config.YTMUSIC_AUTH_FILE)


def search_songs(query: str, limit: int = 20) -> list[dict]:
    """Search YT Music for songs."""
    yt = _get_public_client()
    results = yt.search(query, filter="songs", limit=limit)
    songs = []
    for item in results:
        songs.append({
            "name": item.get("title", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name", "") if item.get("album") else "",
            "duration": item.get("duration", ""),
            "yt_video_id": item.get("videoId", ""),
            "yt_link": f"https://music.youtube.com/watch?v={item.get('videoId', '')}" if item.get("videoId") else "",
        })
    return songs


def get_watch_playlist(video_id: str) -> list[dict]:
    """Get 'Up Next' / radio suggestions for a given song — great for discovery."""
    yt = _get_public_client()
    results = yt.get_watch_playlist(videoId=video_id, limit=25)
    songs = []
    for item in results.get("tracks", []):
        songs.append({
            "name": item.get("title", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])) if item.get("artists") else "",
            "yt_video_id": item.get("videoId", ""),
            "yt_link": f"https://music.youtube.com/watch?v={item.get('videoId', '')}" if item.get("videoId") else "",
        })
    return songs


def add_to_playlist(video_ids: list[str], playlist_id: str = None):
    """Add songs to the configured YT Music playlist. Requires auth."""
    yt = _get_auth_client()
    pid = playlist_id or config.YTMUSIC_PLAYLIST_ID
    yt.add_playlist_items(pid, video_ids)
    return True
