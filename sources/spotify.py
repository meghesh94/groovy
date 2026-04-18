"""Spotify API — search, playlist import, and audio previews."""

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

import config

# ── Clients ────────────────────────────────────────────────────────


def _get_public_client():
    """Client credentials flow — no user auth needed. Works for search, previews, etc."""
    return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
    ))


def _get_user_client():
    """OAuth flow — needs user login. For playlist modification, etc."""
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope=config.SPOTIFY_SCOPE,
        cache_path=".spotify_cache",
    ))


# ── Preview URLs ───────────────────────────────────────────────────


def search_track(name: str, artist: str) -> dict | None:
    """Search Spotify for a track by name + artist. Returns track dict with preview_url or None."""
    sp = _get_public_client()
    query = f"track:{name} artist:{artist}"
    try:
        results = sp.search(q=query, type="track", limit=1)
        items = results["tracks"]["items"]
        if not items:
            # Fallback: simpler query
            results = sp.search(q=f"{name} {artist}", type="track", limit=1)
            items = results["tracks"]["items"]
        if items:
            t = items[0]
            return {
                "spotify_id": t["id"],
                "preview_url": t.get("preview_url", ""),
                "spotify_link": t["external_urls"].get("spotify", ""),
            }
    except Exception as e:
        print(f"  [Spotify] Search failed for '{name} - {artist}': {e}")
    return None


def resolve_preview_urls(tracks: list[dict]) -> list[dict]:
    """Add spotify_id and preview_url to a list of track dicts.

    Searches Spotify for each track by name + artist.
    Modifies tracks in-place and returns them.
    """
    sp = _get_public_client()

    for t in tracks:
        if t.get("preview_url"):
            continue  # Already has preview

        name = t.get("name", "")
        artist = t.get("artist", "")
        if not name:
            continue

        query = f"track:{name} artist:{artist}" if artist else name
        try:
            results = sp.search(q=query, type="track", limit=1)
            items = results["tracks"]["items"]
            if not items:
                results = sp.search(q=f"{name} {artist}", type="track", limit=1)
                items = results["tracks"]["items"]
            if items:
                t["spotify_id"] = items[0]["id"]
                t["preview_url"] = items[0].get("preview_url") or ""
                if not t.get("spotify_link"):
                    t["spotify_link"] = items[0]["external_urls"].get("spotify", "")
        except Exception as e:
            print(f"  [Spotify] Search failed for '{name}': {e}")

    with_preview = sum(1 for t in tracks if t.get("preview_url"))
    print(f"[Spotify] Resolved {with_preview}/{len(tracks)} preview URLs")
    return tracks


def get_playlist_tracks(playlist_id: str) -> list[dict]:
    """Fetch all tracks from a Spotify playlist with preview URLs."""
    sp = _get_public_client()
    tracks = []
    results = sp.playlist_tracks(playlist_id, limit=100)

    while True:
        for item in results["items"]:
            t = item.get("track")
            if not t:
                continue
            tracks.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album": t["album"]["name"],
                "spotify_link": t["external_urls"].get("spotify", ""),
                "spotify_id": t["id"],
                "preview_url": t.get("preview_url") or "",
                "popularity": t["popularity"],
            })
        if results["next"]:
            results = sp.next(results)
        else:
            break

    return tracks


def search_songs(query: str, limit: int = 20) -> list[dict]:
    """Search Spotify and return simplified song dicts."""
    sp = _get_public_client()
    results = sp.search(q=query, type="track", limit=limit)
    songs = []
    for item in results["tracks"]["items"]:
        songs.append({
            "name": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "spotify_link": item["external_urls"].get("spotify", ""),
            "spotify_id": item["id"],
            "preview_url": item.get("preview_url") or "",
            "popularity": item["popularity"],
        })
    return songs


def add_to_playlist(track_uris: list[str], playlist_id: str = None):
    """Add tracks to a Spotify playlist (needs user auth)."""
    sp = _get_user_client()
    pid = playlist_id or config.SPOTIFY_PLAYLIST_ID
    sp.playlist_add_items(pid, track_uris)
    return True
