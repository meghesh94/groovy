/* ── Alpine.js App ────────────────────────────────────────────── */

function sotdApp() {
    return {
        // Profile
        profile: null,
        libraryStats: null,
        playlists: [],
        indexing: false,
        indexedCount: 0,
        indexMessage: '',

        // Discovery config
        config: {
            radio_seeds_count: 8,
            vibe_queries_count: 8,
            artist_vibe_count: 5,
            era_queries_count: 4,
            listen_count: 15,
            final_picks: 5,
            popularity_min: 0,
            popularity_max: 5000000,
            year_min: 0,
            year_max: 2026,
        },

        // Query lists
        queries: null,
        disabledQueries: new Set(),

        // Discovery state
        running: false,
        phase: '',
        phaseMessage: '',
        progressDone: 0,
        progressTotal: 0,
        progressLabel: '',

        // Results
        songs: [],
        picks: new Set(),
        skipped: new Set(),
        approved: new Set(),
        songRatings: {},

        // Audio player
        nowPlaying: null,
        audioEl: null,
        audioProgress: 0,
        audioDuration: 0,
        audioPlaying: false,

        // SSE
        eventSource: null,

        async init() {
            await Promise.all([
                this.loadProfile(),
                this.loadQueries(),
                this.loadLibraryStats(),
                this.loadPlaylists(),
            ]);
            // Poll index status every 5s while indexing
            setInterval(() => { if (this.indexing) this.pollIndexStatus(); }, 5000);
        },

        async loadProfile() {
            try {
                const res = await fetch('/api/profile');
                this.profile = await res.json();
            } catch (e) {
                console.error('Failed to load profile:', e);
            }
        },

        async loadQueries() {
            try {
                const res = await fetch('/api/queries');
                this.queries = await res.json();
            } catch (e) {
                console.error('Failed to load queries:', e);
            }
        },

        async loadLibraryStats() {
            try {
                const res = await fetch('/api/playlists/index-status');
                const data = await res.json();
                this.indexing = data.indexing;
                this.indexedCount = data.indexed_count;
            } catch (e) {
                console.error('Failed to load library stats:', e);
            }
        },

        async loadPlaylists() {
            try {
                const res = await fetch('/api/playlists');
                this.playlists = await res.json();
            } catch (e) {
                console.error('Failed to load playlists:', e);
            }
        },

        // ── Playlist management ─────────────────────────────────

        async addPlaylist(url) {
            if (!url?.trim()) return;
            try {
                const res = await fetch('/api/playlists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url.trim() }),
                });
                const data = await res.json();
                if (!res.ok) { alert(data.error); return; }
                // Poll for the playlist to appear (it imports in background)
                this._pollPlaylists();
            } catch (e) {
                console.error('Failed to add playlist:', e);
            }
        },

        async _pollPlaylists() {
            // Poll every 2s until the playlist count changes
            const before = this.playlists.length;
            for (let i = 0; i < 30; i++) {
                await new Promise(r => setTimeout(r, 2000));
                await this.loadPlaylists();
                if (this.playlists.length > before) {
                    await this.loadProfile();
                    return;
                }
            }
        },

        async removePlaylist(playlistId) {
            try {
                await fetch(`/api/playlists/${playlistId}`, { method: 'DELETE' });
                await this.loadPlaylists();
                await this.loadProfile();
            } catch (e) {
                console.error('Failed to remove playlist:', e);
            }
        },

        async buildIndex() {
            this.indexing = true;
            this.indexMessage = 'Starting...';
            try {
                const res = await fetch('/api/playlists/index', { method: 'POST' });
                const data = await res.json();
                if (!res.ok) { alert(data.error); this.indexing = false; this.indexMessage = ''; return; }
                this.indexMessage = `Indexing ${data.track_count} songs...`;
                this._pollIndexUntilDone();
            } catch (e) {
                console.error('Failed to start indexing:', e);
                this.indexing = false;
                this.indexMessage = 'Failed to start indexing.';
            }
        },

        async pollIndexStatus() {
            try {
                const res = await fetch('/api/playlists/index-status');
                const data = await res.json();
                this.indexing = data.indexing;
                this.indexedCount = data.indexed_count;
                if (this.indexing) {
                    this.indexMessage = `${data.indexed_count} / ${data.total} songs embedded`;
                    if (data.current_song) {
                        this.indexMessage += ` — ${data.current_song}`;
                    }
                }
            } catch (e) {}
        },

        async _pollIndexUntilDone() {
            while (this.indexing) {
                await new Promise(r => setTimeout(r, 3000));
                await this.pollIndexStatus();
            }
            // Final status
            await this.pollIndexStatus();
            this.indexMessage = `Done! ${this.indexedCount} songs indexed.`;
            // Clear the "done" message after 8 seconds
            setTimeout(() => { this.indexMessage = ''; }, 8000);
        },

        toggleQuery(query) {
            if (this.disabledQueries.has(query)) {
                this.disabledQueries.delete(query);
            } else {
                this.disabledQueries.add(query);
            }
        },

        isQueryActive(query) {
            return !this.disabledQueries.has(query);
        },

        profileSongs() {
            return this.profile?.songs || [];
        },

        maxArtistCount() {
            if (!this.profile || !this.profile.top_artists.length) return 1;
            return this.profile.top_artists[0].count;
        },

        // ── Discovery ──────────────────────────────────────────

        async startDiscovery() {
            if (this.running) return;

            this.running = true;
            this.phase = 'starting';
            this.phaseMessage = 'Starting discovery...';
            this.progressDone = 0;
            this.progressTotal = 0;
            this.songs = [];
            this.picks = new Set();
            this.skipped = new Set();
            this.approved = new Set();

            try {
                const res = await fetch('/api/discover', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ...this.config,
                        disabled_queries: [...this.disabledQueries],
                    }),
                });

                if (!res.ok) {
                    const err = await res.json();
                    if (err.error && err.error.includes('already active')) {
                        if (confirm('A previous run is stuck. Reset and try again?')) {
                            await fetch('/api/discover/reset', { method: 'POST' });
                            this.running = false;
                            return this.startDiscovery();
                        }
                    }
                    this.running = false;
                    return;
                }

                this.connectSSE();
            } catch (e) {
                console.error('Discovery start failed:', e);
                this.running = false;
            }
        },

        connectSSE() {
            if (this.eventSource) this.eventSource.close();

            this.eventSource = new EventSource('/api/discover/stream');

            this.eventSource.onmessage = (e) => {
                const data = JSON.parse(e.data);
                this.handleEvent(data);
            };

            this.eventSource.onerror = () => {
                this.eventSource.close();
                if (this.running) {
                    this.running = false;
                    this.phase = 'error';
                    this.phaseMessage = 'Connection lost.';
                }
            };
        },

        handleEvent(event) {
            switch (event.type) {
                case 'status':
                    this.phase = event.phase;
                    this.phaseMessage = event.message;
                    this.progressDone = 0;
                    this.progressTotal = 0;
                    break;

                case 'profile':
                    // Update profile if available
                    if (event.top_artists) {
                        this.profile = {
                            track_count: event.track_count,
                            top_artists: event.top_artists,
                            top_genres: event.top_genres,
                        };
                    }
                    break;

                case 'progress':
                    this.progressDone = event.done;
                    this.progressTotal = event.total;
                    this.progressLabel = event.query || event.song || '';
                    break;

                case 'dedup':
                    this.phaseMessage = `${event.unique_count} unique songs from ${event.raw_count} raw`;
                    break;

                case 'result':
                    this.songs.push(event.song);
                    break;

                case 'complete':
                    this.running = false;
                    this.phase = 'complete';
                    this.phaseMessage = event.message;
                    if (event.picks) {
                        this.picks = new Set(event.picks);
                    }
                    if (this.eventSource) this.eventSource.close();
                    break;

                case 'error':
                    this.running = false;
                    this.phase = 'error';
                    this.phaseMessage = event.message;
                    if (this.eventSource) this.eventSource.close();
                    break;

                case 'warning':
                    console.warn('[Discovery]', event.message);
                    break;
            }
        },

        progressPercent() {
            if (!this.progressTotal) return 0;
            return Math.round((this.progressDone / this.progressTotal) * 100);
        },

        // ── Song actions ───────────────────────────────────────

        async rateSong(songId, rating) {
            // Clicking the same star again = unrate
            if (this.songRatings[songId] === rating) {
                delete this.songRatings[songId];
                return;
            }
            this.songRatings[songId] = rating;
            try {
                const res = await fetch(`/api/songs/${songId}/rate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rating }),
                });
                const data = await res.json();
                // Songs rated 4+ auto-approve into library
                if (rating >= 4 && !this.approved.has(songId)) {
                    this.approved.add(songId);
                    await Promise.all([this.loadProfile(), this.loadPlaylists(), this.loadQueries()]);
                }
            } catch (e) {
                console.error('Rate failed:', e);
            }
        },

        async approveSong(songId) {
            try {
                await fetch(`/api/songs/${songId}/approve`, { method: 'POST' });
                this.approved.add(songId);
                // Refresh profile and playlists — approved song feeds back into taste
                await Promise.all([this.loadProfile(), this.loadPlaylists(), this.loadQueries()]);
            } catch (e) {
                console.error('Approve failed:', e);
            }
        },

        async skipSong(songId) {
            try {
                await fetch(`/api/songs/${songId}/skip`, { method: 'POST' });
                this.skipped.add(songId);
            } catch (e) {
                console.error('Skip failed:', e);
            }
        },

        isSongPicked(songId) { return this.picks.has(songId); },
        isSongSkipped(songId) { return this.skipped.has(songId); },
        isSongApproved(songId) { return this.approved.has(songId); },

        ytThumb(videoId) {
            return `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`;
        },

        // ── Audio Player ───────────────────────────────────────

        playSong(song) {
            if (this.nowPlaying?._id === song._id && this.audioPlaying) {
                this.pauseAudio();
                return;
            }

            this.nowPlaying = song;

            if (!this.audioEl) {
                this.audioEl = new Audio();
                this.audioEl.addEventListener('timeupdate', () => {
                    this.audioProgress = this.audioEl.currentTime;
                    this.audioDuration = this.audioEl.duration || 0;
                });
                this.audioEl.addEventListener('ended', () => {
                    this.audioPlaying = false;
                });
            }

            this.audioEl.src = `/audio/${song.yt_video_id}.wav`;
            this.audioEl.play();
            this.audioPlaying = true;
        },

        pauseAudio() {
            if (this.audioEl) {
                this.audioEl.pause();
                this.audioPlaying = false;
            }
        },

        seekAudio(event) {
            if (!this.audioEl || !this.audioDuration) return;
            const bar = event.currentTarget;
            const rect = bar.getBoundingClientRect();
            const pct = (event.clientX - rect.left) / rect.width;
            this.audioEl.currentTime = pct * this.audioDuration;
        },

        formatTime(sec) {
            if (!sec || isNaN(sec)) return '0:00';
            const m = Math.floor(sec / 60);
            const s = Math.floor(sec % 60);
            return `${m}:${s.toString().padStart(2, '0')}`;
        },

        isCurrentlyPlaying(songId) {
            return this.nowPlaying?._id === songId && this.audioPlaying;
        },

        formatViews(n) {
            if (n == null) return '';
            if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return Math.round(n / 1000) + 'K';
            return String(n);
        },

        formatMaxViews(n) {
            if (n >= 1000000) return (n / 1000000).toFixed(0) + 'M';
            if (n >= 1000) return Math.round(n / 1000) + 'K';
            return String(n);
        },
    };
}
