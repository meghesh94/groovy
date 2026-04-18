/* ── Groovy — Daily Drop App ──────────────────────────────────── */

function groovyApp() {
    return {
        // Auth
        user: null,

        // Navigation
        currentView: 'today',  // 'today' | 'collection' | 'settings'

        // Drop state
        dropState: 'empty',    // 'empty' | 'generating' | 'audition' | 'done'
        dropSongs: [],
        dropIndex: 0,
        lastDrop: null,
        hoverStar: 0,

        // Generation progress (reused from old SSE flow)
        phase: '',
        phaseMessage: '',
        progressDone: 0,
        progressTotal: 0,
        progressLabel: '',
        eventSource: null,

        // Collection
        collectionSongs: [],

        // Settings: playlists
        playlists: [],
        hasPlaylists: false,
        indexing: false,
        indexedCount: 0,
        indexMessage: '',

        // Settings: profile
        profile: null,

        // Settings: discovery config
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

        // Song ratings (local cache)
        songRatings: {},

        // Audio player
        nowPlaying: null,
        audioEl: null,
        audioProgress: 0,
        audioDuration: 0,
        audioPlaying: false,

        // ── Init ──────────────────────────────────────────────

        async init() {
            await this.loadUser();
            if (!this.user) return;

            await Promise.all([
                this.loadPlaylists(),
                this.loadProfile(),
                this.loadLibraryStats(),
                this.loadDrop(),
                this.loadDropHistory(),
            ]);

            setInterval(() => { if (this.indexing) this.pollIndexStatus(); }, 5000);
        },

        async loadUser() {
            try {
                const res = await fetch('/api/me');
                const data = await res.json();
                this.user = data.logged_in ? data : null;
            } catch (e) {
                console.error('Failed to load user:', e);
            }
        },

        // ── Drop: Today View ─────────────────────────────────

        async loadDrop() {
            try {
                const res = await fetch('/api/drop');
                const data = await res.json();
                this.dropSongs = data.songs;

                if (data.total === 0) {
                    this.dropState = 'empty';
                } else if (data.unreviewed === 0) {
                    // All reviewed — show summary
                    this._syncRatingsFromSongs();
                    this.dropState = 'done';
                } else {
                    // Find first unreviewed song
                    this._syncRatingsFromSongs();
                    this.dropIndex = this.dropSongs.findIndex(s => s.status === 'discovered');
                    if (this.dropIndex < 0) this.dropIndex = 0;
                    this.dropState = 'audition';
                    // Auto-play after a tick
                    this.$nextTick(() => this.playSong(this.currentDropSong()));
                }
            } catch (e) {
                console.error('Failed to load drop:', e);
                this.dropState = 'empty';
            }
        },

        async loadDropHistory() {
            try {
                const res = await fetch('/api/drop/history');
                const data = await res.json();
                if (data.length > 0) {
                    this.lastDrop = data[0];
                }
            } catch (e) {}
        },

        _syncRatingsFromSongs() {
            for (const s of this.dropSongs) {
                if (s.rating > 0) this.songRatings[s._id] = s.rating;
            }
        },

        currentDropSong() {
            return this.dropSongs[this.dropIndex] || null;
        },

        dropLikedCount() {
            return this.dropSongs.filter(s => s.status === 'approved').length;
        },

        dropSkippedCount() {
            return this.dropSongs.filter(s => s.status === 'skipped').length;
        },

        async generateDrop() {
            this.dropState = 'generating';
            this.phase = 'starting';
            this.phaseMessage = 'Starting discovery...';
            this.progressDone = 0;
            this.progressTotal = 0;

            try {
                const res = await fetch('/api/discover', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.config),
                });

                if (!res.ok) {
                    const err = await res.json();
                    if (err.error && err.error.includes('already active')) {
                        if (confirm('A previous run is stuck. Reset and try again?')) {
                            await fetch('/api/discover/reset', { method: 'POST' });
                            return this.generateDrop();
                        }
                    }
                    this.dropState = 'empty';
                    return;
                }

                this.connectSSE();
            } catch (e) {
                console.error('Generate failed:', e);
                this.dropState = 'empty';
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
                if (this.dropState === 'generating') {
                    this.dropState = 'empty';
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
                case 'progress':
                    this.progressDone = event.done;
                    this.progressTotal = event.total;
                    this.progressLabel = event.query || event.song || '';
                    break;
                case 'dedup':
                    this.phaseMessage = `${event.unique_count} unique songs from ${event.raw_count} raw`;
                    break;
                case 'complete':
                    if (this.eventSource) this.eventSource.close();
                    // Load the tagged drop from the backend
                    setTimeout(() => this.loadDrop(), 300);
                    break;
                case 'error':
                    if (this.eventSource) this.eventSource.close();
                    this.dropState = 'empty';
                    this.phaseMessage = event.message;
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

        // ── Audition Actions ─────────────────────────────────

        async rateAndNext(rating) {
            const song = this.currentDropSong();
            if (!song) return;

            this.songRatings[song._id] = rating;
            song.rating = rating;
            song.status = rating >= 4 ? 'approved' : 'rated';

            try {
                await fetch(`/api/songs/${song._id}/rate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rating }),
                });
            } catch (e) {
                console.error('Rate failed:', e);
            }

            // Brief pause to show the rating, then advance
            await new Promise(r => setTimeout(r, 400));
            this.hoverStar = 0;
            this._advanceDrop();
        },

        async skipAndNext() {
            const song = this.currentDropSong();
            if (!song) return;

            song.status = 'skipped';

            try {
                await fetch(`/api/songs/${song._id}/skip`, { method: 'POST' });
            } catch (e) {
                console.error('Skip failed:', e);
            }

            this._advanceDrop();
        },

        _advanceDrop() {
            // Find next unreviewed song
            const next = this.dropSongs.findIndex((s, i) => i > this.dropIndex && s.status === 'discovered');
            if (next >= 0) {
                this.dropIndex = next;
                this.$nextTick(() => this.playSong(this.currentDropSong()));
            } else {
                // All done
                this.dropState = 'done';
                this.pauseAudio();
            }
        },

        // ── Collection ───────────────────────────────────────

        async loadCollection() {
            try {
                const res = await fetch('/api/collection');
                const data = await res.json();
                this.collectionSongs = data.songs;
            } catch (e) {
                console.error('Failed to load collection:', e);
            }
        },

        // ── Settings: Playlists ──────────────────────────────

        async loadPlaylists() {
            try {
                const res = await fetch('/api/playlists');
                this.playlists = await res.json();
                this.hasPlaylists = this.playlists.length > 0;
            } catch (e) {
                console.error('Failed to load playlists:', e);
            }
        },

        async loadProfile() {
            try {
                const res = await fetch('/api/profile');
                this.profile = await res.json();
            } catch (e) {}
        },

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
                this._pollPlaylists();
            } catch (e) {
                console.error('Failed to add playlist:', e);
            }
        },

        async _pollPlaylists() {
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
                this.indexing = false;
                this.indexMessage = 'Failed.';
            }
        },

        async loadLibraryStats() {
            try {
                const res = await fetch('/api/playlists/index-status');
                const data = await res.json();
                this.indexing = data.indexing;
                this.indexedCount = data.indexed_count;
            } catch (e) {}
        },

        async pollIndexStatus() {
            try {
                const res = await fetch('/api/playlists/index-status');
                const data = await res.json();
                this.indexing = data.indexing;
                this.indexedCount = data.indexed_count;
                if (this.indexing) {
                    this.indexMessage = `${data.indexed_count} / ${data.total} embedded`;
                    if (data.current_song) this.indexMessage += ` — ${data.current_song}`;
                }
            } catch (e) {}
        },

        async _pollIndexUntilDone() {
            while (this.indexing) {
                await new Promise(r => setTimeout(r, 3000));
                await this.pollIndexStatus();
            }
            await this.pollIndexStatus();
            this.indexMessage = `Done! ${this.indexedCount} songs indexed.`;
            setTimeout(() => { this.indexMessage = ''; }, 8000);
        },

        maxArtistCount() {
            if (!this.profile || !this.profile.top_artists?.length) return 1;
            return this.profile.top_artists[0].count;
        },

        // ── Audio Player ─────────────────────────────────────

        playSong(song) {
            if (!song) return;
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
            this.audioEl.play().catch(() => {
                // Browser blocked auto-play — user needs to click play
                this.audioPlaying = false;
            });
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

        isCurrentlyPlaying(songId) {
            return this.nowPlaying?._id === songId && this.audioPlaying;
        },

        // ── Formatters ───────────────────────────────────────

        formatTime(sec) {
            if (!sec || isNaN(sec)) return '0:00';
            const m = Math.floor(sec / 60);
            const s = Math.floor(sec % 60);
            return `${m}:${s.toString().padStart(2, '0')}`;
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

        ytThumb(videoId) {
            return `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`;
        },
    };
}
