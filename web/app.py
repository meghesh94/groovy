"""Flask web app for Groovy."""

import functools
import json
import os
import sys
import threading
import webbrowser
from collections import Counter
from datetime import date
from queue import Empty

from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for

# Add parent dir to path so we can import the existing modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from web import db
from web.discovery_runner import RunConfig, get_event_queue, is_running, start_discovery, force_reset

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = config.SECRET_KEY

# Support running behind reverse proxy (HuggingFace Spaces, etc.)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Initialize database on import
db.init()

# ── Google OAuth ───────────────────────────────────────────────────

oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def current_user_id() -> str:
    return session["user_id"]


# ── Auth routes ────────────────────────────────────────────────────

@app.route("/auth/login")
def login():
    # Dev bypass: skip Google OAuth when credentials aren't configured
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        user = db.upsert_user(
            user_id="dev-user",
            email="dev@groovy.local",
            name="Dev User",
            picture="",
        )
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["user_picture"] = user["picture"]
        return redirect("/")

    redirect_uri = url_for("auth_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        return "Login failed", 400

    user = db.upsert_user(
        user_id=userinfo["sub"],
        email=userinfo["email"],
        name=userinfo.get("name", ""),
        picture=userinfo.get("picture", ""),
    )
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_picture"] = user["picture"]
    return redirect("/")


@app.route("/auth/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/api/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "user_id": session["user_id"],
        "name": session.get("user_name", ""),
        "picture": session.get("user_picture", ""),
    })


# ── Background indexing state (transient) ──────────────────────────

_indexing = False
_index_progress = {"done": 0, "total": 0, "current_song": ""}


def _get_all_tracks(user_id: str) -> list[dict]:
    """Get all tracks across all playlists + approved songs (deduped)."""
    tracks = db.get_all_tracks(user_id)
    approved = db.get_approved_tracks(user_id)

    seen = {(t["name"].lower().strip(), t["artist"].lower().strip()) for t in tracks}
    for t in approved:
        key = (t["name"].lower().strip(), t["artist"].lower().strip())
        if key not in seen:
            if t.get("yt_video_id"):
                t["youtube_link"] = f"https://www.youtube.com/watch?v={t['yt_video_id']}"
                t["yt_link"] = t["youtube_link"]
            seen.add(key)
            tracks.append(t)

    return tracks


def _build_profile_from_tracks(tracks: list[dict]) -> dict:
    artist_counter = Counter()
    genre_counter = Counter()
    for t in tracks:
        if t.get("artist"):
            artist_counter[t["artist"]] += 1
        for g in t.get("genres", []):
            genre_counter[g] += 1

    return {
        "tracks": tracks,
        "track_count": len(tracks),
        "top_artists": [{"name": n, "count": c} for n, c in artist_counter.most_common(20)],
        "top_genres": [g for g, _ in genre_counter.most_common(20)],
    }


# ── Pages ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── API: Audio playback ─────────────────────────────────────────

AUDIO_CACHE_DIR = os.environ.get(
    "GROOVY_DATA_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")),
) + "/audio_cache"

@app.route("/audio/<video_id>.wav")
def serve_audio(video_id):
    return send_from_directory(AUDIO_CACHE_DIR, f"{video_id}.wav", mimetype="audio/wav")


# ── API: Profile ────────────────────────────────────────────────────

@app.route("/api/profile")
@login_required
def api_profile():
    uid = current_user_id()
    tracks = _get_all_tracks(uid)
    if not tracks:
        return jsonify({
            "track_count": 0,
            "top_artists": [],
            "top_genres": [],
            "source": "none",
        })
    profile = _build_profile_from_tracks(tracks)
    songs_for_grid = [
        {"name": t["name"], "artist": t["artist"], "yt_video_id": t["yt_video_id"]}
        for t in tracks if t.get("yt_video_id")
    ]
    return jsonify({
        "track_count": profile["track_count"],
        "top_artists": profile["top_artists"][:15],
        "top_genres": profile["top_genres"][:15],
        "songs": songs_for_grid,
        "source": "playlists",
    })


# ── API: Playlists ──────────────────────────────────────────────────

@app.route("/api/playlists")
@login_required
def api_playlists():
    uid = current_user_id()
    playlists = db.get_playlists(uid)
    return jsonify([{
        "url": pl["url"],
        "platform": pl["platform"],
        "title": pl["title"],
        "track_count": pl["track_count"],
        "playlist_id": pl["id"],
    } for pl in playlists])


@app.route("/api/playlists", methods=["POST"])
@login_required
def api_add_playlist():
    uid = current_user_id()
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    from sources.playlist_import import parse_playlist_url
    parsed = parse_playlist_url(url)
    if not parsed:
        return jsonify({"error": "Unrecognized playlist URL. Supports Spotify and YT Music playlists."}), 400

    if db.get_playlist(parsed["playlist_id"]):
        return jsonify({"error": "Playlist already added."}), 409

    def _fetch():
        try:
            from sources.playlist_import import fetch_playlist
            result = fetch_playlist(url)
            db.add_playlist(
                user_id=uid,
                playlist_id=result["playlist_id"],
                url=result["url"],
                platform=result["platform"],
                title=result["title"],
            )
            db.add_playlist_tracks(result["playlist_id"], result.get("tracks", []))
            print(f"[Playlist] Added: {result['title']} ({result['track_count']} tracks)")
        except Exception as e:
            print(f"[Playlist] Failed to fetch {url}: {e}")

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "Importing playlist...", "playlist_id": parsed["playlist_id"]})


@app.route("/api/playlists/<playlist_id>", methods=["DELETE"])
@login_required
def api_remove_playlist(playlist_id):
    db.remove_playlist(current_user_id(), playlist_id)
    return jsonify({"ok": True})


@app.route("/api/playlists/index", methods=["POST"])
@login_required
def api_index_playlists():
    global _indexing
    if _indexing:
        return jsonify({"error": "Indexing already in progress"}), 409

    tracks = _get_all_tracks(current_user_id())
    if not tracks:
        return jsonify({"error": "No tracks to index. Add playlists first."}), 400

    _indexing = True

    def _do_index():
        global _indexing
        try:
            from sources.mert_ear import build_library_index

            def _on_progress(done, total, song_name):
                _index_progress["done"] = done
                _index_progress["total"] = total
                _index_progress["current_song"] = song_name

            _index_progress["total"] = len(tracks)
            build_library_index(tracks, force=True, on_progress=_on_progress)
            print(f"[MERT] Index rebuilt with {len(tracks)} tracks")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[MERT] Indexing failed: {e}")
        finally:
            _indexing = False

    thread = threading.Thread(target=_do_index, daemon=True)
    thread.start()

    return jsonify({"ok": True, "track_count": len(tracks), "message": "Indexing started..."})


@app.route("/api/playlists/index-status")
@login_required
def api_index_status():
    if _indexing:
        return jsonify({
            "indexing": True,
            "indexed_count": _index_progress["done"],
            "total": _index_progress["total"],
            "current_song": _index_progress["current_song"],
        })

    from sources.mert_ear import LIBRARY_INDEX_PATH
    import numpy as np
    indexed_count = 0
    if LIBRARY_INDEX_PATH.exists():
        data = np.load(LIBRARY_INDEX_PATH, allow_pickle=True)
        songs = json.loads(str(data["songs"]))
        indexed_count = len(songs)
    return jsonify({
        "indexing": False,
        "indexed_count": indexed_count,
        "total": indexed_count,
        "current_song": "",
    })


# ── API: Queries ────────────────────────────────────────────────────

@app.route("/api/queries")
@login_required
def api_queries():
    tracks = _get_all_tracks(current_user_id())

    seeds = [{"query": f"{t['name']} {t['artist']}", "name": t["name"], "artist": t["artist"]}
             for t in tracks if t.get("name") and t.get("artist")]

    seen = set()
    artists = []
    for t in tracks:
        a = t.get("artist", "").strip()
        if a and a not in seen:
            artists.append(f"artists similar to {a}")
            seen.add(a)

    return jsonify({
        "radio_seeds": seeds,
        "vibe_queries": [],
        "artist_vibe_queries": artists,
        "era_queries": [],
    })


# ── API: Discovery ──────────────────────────────────────────────────

@app.route("/api/discover/reset", methods=["POST"])
@login_required
def api_discover_reset():
    force_reset()
    return jsonify({"ok": True})


# Track which date a discovery run is generating for
_drop_date_for_run = {}


@app.route("/api/discover", methods=["POST"])
@login_required
def api_discover():
    if is_running():
        return jsonify({"error": "A discovery run is already active."}), 409

    body = request.get_json(silent=True) or {}

    run_config = RunConfig(
        radio_seeds_count=body.get("radio_seeds_count", 8),
        vibe_queries_count=body.get("vibe_queries_count", 8),
        artist_vibe_count=body.get("artist_vibe_count", 5),
        era_queries_count=body.get("era_queries_count", 4),
        listen_count=body.get("listen_count", 15),
        final_picks=body.get("final_picks", 5),
        popularity_min=body.get("popularity_min", 0),
        popularity_max=body.get("popularity_max", 5_000_000),
        year_min=body.get("year_min", 0),
        year_max=body.get("year_max", 2026),
        disabled_queries=body.get("disabled_queries", []),
        skip_words=body.get("skip_words", RunConfig().skip_words),
    )

    uid = current_user_id()
    tracks = _get_all_tracks(uid)
    run_id = start_discovery(run_config, library_tracks=tracks if tracks else None)

    # Remember the drop date for this run (capture now, not at completion)
    _drop_date_for_run[uid] = date.today().isoformat()

    return jsonify({"run_id": run_id})


@app.route("/api/discover/stream")
@login_required
def api_discover_stream():
    uid = current_user_id()

    def event_stream():
        queue = get_event_queue()
        while True:
            try:
                event = queue.get(timeout=30)
            except Empty:
                yield ":\n\n"  # SSE keepalive
                continue

            if event.get("type") == "result" and "song" in event:
                db.save_song(uid, event["song"])

            # Tag picks with drop_date before yielding the complete event
            if event.get("type") == "complete" and event.get("picks"):
                drop_date = _drop_date_for_run.pop(uid, date.today().isoformat())
                db.tag_drop(uid, event["picks"], drop_date)

            yield f"data: {json.dumps(event)}\n\n"

            if event.get("type") in ("complete", "error"):
                break

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── API: Song actions ───────────────────────────────────────────────

@app.route("/api/songs/<song_id>/approve", methods=["POST"])
@login_required
def api_approve(song_id):
    uid = current_user_id()
    song = db.get_song(uid, song_id)
    if not song:
        return jsonify({"error": "Song not found"}), 404

    db.update_song_status(uid, song_id, "approved")
    return jsonify({"ok": True, "song": song["name"]})


@app.route("/api/songs/<song_id>/rate", methods=["POST"])
@login_required
def api_rate(song_id):
    uid = current_user_id()
    song = db.get_song(uid, song_id)
    if not song:
        return jsonify({"error": "Song not found"}), 404

    body = request.get_json(silent=True) or {}
    rating = body.get("rating", 0)
    db.update_song_rating(uid, song_id, rating)

    return jsonify({"ok": True, "song": song["name"], "rating": rating})


@app.route("/api/songs/<song_id>/skip", methods=["POST"])
@login_required
def api_skip(song_id):
    db.update_song_status(current_user_id(), song_id, "skipped")
    return jsonify({"ok": True})


# ── API: Drops ─────────────────────────────────────────────────────

@app.route("/api/drop")
@login_required
def api_drop():
    uid = current_user_id()
    drop_date = request.args.get("date", date.today().isoformat())
    songs = db.get_drop(uid, drop_date)
    reviewed = sum(1 for s in songs if s["status"] != "discovered")
    return jsonify({
        "date": drop_date,
        "songs": songs,
        "total": len(songs),
        "reviewed": reviewed,
        "unreviewed": len(songs) - reviewed,
    })


@app.route("/api/drop/history")
@login_required
def api_drop_history():
    return jsonify(db.get_drop_dates(current_user_id()))


@app.route("/api/collection")
@login_required
def api_collection():
    return jsonify({"songs": db.get_collection(current_user_id())})


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
    print(f"\n  Groovy → http://localhost:{port}\n")
    if not os.environ.get("PORT"):
        webbrowser.open(f"http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
