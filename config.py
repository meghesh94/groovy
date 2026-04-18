import os
from dotenv import load_dotenv

load_dotenv()

# Spotify
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SPOTIFY_PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")  # playlist to publish TO
SPOTIFY_SOURCE_PLAYLIST_ID = os.getenv("SPOTIFY_SOURCE_PLAYLIST_ID")  # optional, if you want to pull from Spotify directly
SPOTIFY_SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"

# YT Music
YTMUSIC_AUTH_FILE = os.getenv("YTMUSIC_AUTH_FILE", "oauth.json")
YTMUSIC_PLAYLIST_ID = os.getenv("YTMUSIC_PLAYLIST_ID")

