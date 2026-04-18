"""Spotify search and playlist management."""

import spotipy
from spotipy.oauth2 import SpotifyOAuth

import config


def _get_client():
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope=config.SPOTIFY_SCOPE,
        cache_path=".spotify_cache",
    ))


def get_playlist_tracks(playlist_id: str = None) -> list[dict]:
    """Fetch all tracks from a Spotify playlist (your existing library).

    Returns full track details including artist IDs for seeding recommendations.
    """
    sp = _get_client()
    pid = playlist_id or config.SPOTIFY_SOURCE_PLAYLIST_ID
    tracks = []
    results = sp.playlist_tracks(pid, limit=100)

    while True:
        for item in results["items"]:
            t = item.get("track")
            if not t:
                continue
            tracks.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "artist_ids": [a["id"] for a in t["artists"]],
                "album": t["album"]["name"],
                "release_year": t["album"]["release_date"][:4] if t["album"].get("release_date") else "",
                "spotify_link": t["external_urls"].get("spotify", ""),
                "spotify_uri": t["uri"],
                "track_id": t["id"],
                "popularity": t["popularity"],
            })
        if results["next"]:
            results = sp.next(results)
        else:
            break

    return tracks


def get_artist_genres(artist_ids: list[str]) -> dict[str, list[str]]:
    """Get genres for a batch of artists. Returns {artist_id: [genres]}."""
    sp = _get_client()
    genres = {}
    # Spotify allows 50 artists per request
    for i in range(0, len(artist_ids), 50):
        batch = artist_ids[i:i+50]
        results = sp.artists(batch)
        for a in results["artists"]:
            if a:
                genres[a["id"]] = a.get("genres", [])
    return genres


def search_songs(query: str, limit: int = 20) -> list[dict]:
    """Search Spotify and return simplified song dicts."""
    sp = _get_client()
    results = sp.search(q=query, type="track", limit=limit)
    songs = []
    for item in results["tracks"]["items"]:
        songs.append({
            "name": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "release_year": item["album"]["release_date"][:4] if item["album"]["release_date"] else "",
            "spotify_link": item["external_urls"].get("spotify", ""),
            "spotify_uri": item["uri"],
            "duration_ms": item["duration_ms"],
            "popularity": item["popularity"],
        })
    return songs


def get_recommendations(seed_tracks: list[str] = None, seed_artists: list[str] = None,
                        seed_genres: list[str] = None, limit: int = 20) -> list[dict]:
    """Get Spotify recommendations based on seeds."""
    sp = _get_client()
    kwargs = {"limit": limit}
    if seed_tracks:
        kwargs["seed_tracks"] = seed_tracks[:5]
    if seed_artists:
        kwargs["seed_artists"] = seed_artists[:5]
    if seed_genres:
        kwargs["seed_genres"] = seed_genres[:5]

    results = sp.recommendations(**kwargs)
    songs = []
    for item in results["tracks"]:
        songs.append({
            "name": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "release_year": item["album"]["release_date"][:4] if item["album"]["release_date"] else "",
            "spotify_link": item["external_urls"].get("spotify", ""),
            "spotify_uri": item["uri"],
            "duration_ms": item["duration_ms"],
            "popularity": item["popularity"],
        })
    return songs


def get_audio_features(track_ids: list[str]) -> list[dict]:
    """Get audio features (tempo, energy, danceability, etc.) for tracks."""
    sp = _get_client()
    features = sp.audio_features(track_ids)
    return [f for f in features if f is not None]


def add_to_playlist(track_uris: list[str], playlist_id: str = None):
    """Add tracks to the configured Spotify playlist."""
    sp = _get_client()
    pid = playlist_id or config.SPOTIFY_PLAYLIST_ID
    sp.playlist_add_items(pid, track_uris)
    return True


def get_artist_top_tracks(artist_id: str, country: str = "US") -> list[dict]:
    """Get top tracks for an artist — useful for discovery."""
    sp = _get_client()
    results = sp.artist_top_tracks(artist_id, country=country)
    return [{
        "name": t["name"],
        "artist": ", ".join(a["name"] for a in t["artists"]),
        "spotify_uri": t["uri"],
        "spotify_link": t["external_urls"].get("spotify", ""),
        "popularity": t["popularity"],
    } for t in results["tracks"]]
