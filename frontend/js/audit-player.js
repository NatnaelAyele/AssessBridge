/* Multipart recording playback for auditor reports. */
window.AuditPlayer = class {
  constructor(host, parts, base, options = {}) {
    this.host = host; this.base = base; this.options = options; this.event = options.event;
    this.parts = parts.filter(p => p.total_bytes > 0).map(p => ({ ...p, duration: Math.max(.2, (Date.parse(p.finalized_at) - Date.parse(p.started_at)) / 1000 || 1) }));
    this.feedback = options.feedback; this.messageId = `player-${++window.AuditPlayer.sequence}`;
    this.index = 0; this.loop = !!this.event; this.disposed = false;
    const node = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls || ""; if (text !== undefined) e.textContent = text; return e; };
    const button = (text, callback, cls = "secondary-button") => { const b = node("button", cls, text); b.type = "button"; b.addEventListener("click", callback); return b; };
    this.wrap = node("div", this.event ? "review-inline-player" : "review-full-player"); this.wrap.tabIndex = 0;
    this.wrap.setAttribute("aria-label", this.event ? "Flag evidence player" : "Full session recording player");
    if (this.event) {
      const head = node("div", "review-inline-head"); head.append(node("strong", "", "Flag evidence"), button("Close video", () => { this.destroy(); options.onClose?.(); })); this.wrap.append(head);
    }
    this.video = node("video"); this.video.playsInline = true; this.video.preload = "metadata";
    this.video.tabIndex = 0; this.wrap.append(this.video);
    const controls = node("div", `review-player-controls${this.event ? "" : " review-player-integrated"}`);
    this.track = node("input", "review-video-range"); this.track.type = "range"; this.track.min = "0"; this.track.step = ".1"; this.track.setAttribute("aria-label", "Recording position");
    const row = node("div", "review-player-buttons");
    this.playButton = button("Play", () => this.togglePlay());
    const back = button("−10s", () => this.seek(this.position() - 10)), forward = button("+10s", () => this.seek(this.position() + 10));
    this.clock = node("span", "review-player-time"); this.muteButton = button("Mute", () => { this.video.muted = !this.video.muted; });
    this.volume = node("input", "review-player-volume"); this.volume.type = "range"; this.volume.min = "0"; this.volume.max = "1"; this.volume.step = ".05"; this.volume.value = "1"; this.volume.setAttribute("aria-label", "Volume");
    row.append(this.playButton, back, forward, this.clock, this.muteButton, this.volume);
    if (this.event) {
      this.loopButton = button("Loop on", () => { this.loop = !this.loop; if (this.loop) this.load(this.eventIndex, this.event.start_ms / 1000, true); this.update(); }); row.append(this.loopButton);
    } else {
      this.speed = button("1x", () => { this.video.playbackRate = this.video.playbackRate >= 2 ? .75 : this.video.playbackRate + .25; this.update(); }, "review-player-speed");
      const full = button("⛶", () => this.fullscreen(), "review-player-fullscreen"); full.setAttribute("aria-label", "Toggle fullscreen"); row.append(this.speed, full);
    }
    controls.append(this.track, row); this.wrap.append(controls); host.append(this.wrap);
    this.error = node("div", "review-player-feedback"); this.wrap.append(this.error);
    this.track.addEventListener("input", () => this.seek(Number(this.track.value)));
    this.volume.addEventListener("input", () => { this.video.volume = Number(this.volume.value); this.video.muted = false; });
    this.video.addEventListener("click", () => this.togglePlay());
    this.video.addEventListener("pointerdown", () => this.wrap.focus());
    this.video.addEventListener("error", () => { this.showError("Could not play this recording. Close the player and open it again."); });
    for (const event of ["timeupdate", "loadedmetadata", "play", "pause", "volumechange", "ratechange", "seeking"]) this.video.addEventListener(event, () => this.update());
    this.video.addEventListener("ended", () => {
      if (this.loop) this.load(this.eventIndex, this.event.start_ms / 1000, true);
      else if (this.index < this.parts.length - 1) this.load(this.index + 1, 0, true);
    });
    this.wrap.addEventListener("keydown", e => this.key(e));
    if (this.event) {
      this.eventIndex = this.parts.findIndex(p => p.id === this.event.part_id);
      if (this.eventIndex < 0) { this.showError("Evidence recording unavailable."); return; }
      this.timer = setInterval(() => {
        if (!this.loop || this.video.paused || this.video.seeking) return;
        if (this.position() >= this.bounds()[1] - .05) this.load(this.eventIndex, this.event.start_ms / 1000, true);
      }, 80);
      this.load(this.eventIndex, this.event.start_ms / 1000, true);
    } else if (this.parts.length) this.load(0, 0, false);
    else this.showError("Recording unavailable.");
    this.update();
  }
  prefix(index) { return this.parts.slice(0, index).reduce((n, p) => n + p.duration, 0); }
  duration() { return this.prefix(this.parts.length); }
  position() { return this.prefix(this.index) + (this.video.currentTime || 0); }
  bounds() {
    const part = this.parts[this.eventIndex], offset = this.prefix(this.eventIndex);
    return [offset + Math.min(this.event.start_ms / 1000, Math.max(0, part.duration - .2)), offset + Math.min(part.duration, Math.max(this.event.start_ms / 1000 + .1, this.event.end_ms / 1000))];
  }
  load(index, seconds, autoplay) {
    const part = this.parts[index]; if (!part || this.disposed) return; this.index = index;
    const ready = () => {
      if (this.disposed || this.video.dataset.part !== String(part.id)) return;
      if (Number.isFinite(this.video.duration) && this.video.duration > 0) part.duration = this.video.duration;
      this.video.currentTime = Math.min(Math.max(0, seconds), Math.max(0, part.duration - .05));
      this.update(); if (autoplay) { this.options.beforePlay?.(); this.play(); }
    };
    if (this.video.dataset.part !== String(part.id)) {
      this.video.pause(); this.video.dataset.part = String(part.id); this.video.onloadedmetadata = ready;
      this.video.src = `${this.base}/recording/parts/${part.id}`; this.video.load();
    } else if (this.video.readyState >= 1) ready(); else this.video.onloadedmetadata = ready;
  }
  seek(seconds) {
    if (!this.parts.length || (this.event && this.eventIndex < 0)) return;
    const target = Math.min(Math.max(0, seconds), Math.max(0, this.duration() - .05)), playing = !this.video.paused;
    if (this.event) { const [start, end] = this.bounds(); this.loop = target >= start && target < end; }
    let offset = 0;
    for (let index = 0; index < this.parts.length; index++) {
      if (target < offset + this.parts[index].duration || index === this.parts.length - 1) { this.load(index, target - offset, playing); break; }
      offset += this.parts[index].duration;
    }
    this.update();
  }
  showError(text) {
    if (!this.disposed && this.feedback?.active) this.feedback.show({ id: this.messageId, target: this.error, tone: 'error', text });
  }
  play() {
    const source = this.video.src;
    this.video.play().then(() => { if (!this.disposed && this.video.src === source) this.feedback?.clear(this.messageId); }).catch(error => {
      if (this.disposed || this.video.src !== source || error.name === 'AbortError') return;
      this.showError(error.name === 'NotAllowedError' ? 'Playback was blocked. Select Play to try again.' : 'Could not play this recording. Close the player and open it again.');
    });
  }
  togglePlay() { if (this.video.paused) { this.options.beforePlay?.(); this.play(); } else this.video.pause(); }
  pause() { this.video.pause(); }
  async fullscreen() {
    try {
      if (document.fullscreenElement === this.wrap) await document.exitFullscreen();
      else if (this.wrap.requestFullscreen) await this.wrap.requestFullscreen();
      else throw Error('Unavailable');
    } catch { this.showError('Fullscreen is unavailable. You can continue watching here.'); }
  }
  update() {
    if (this.disposed) return;
    const length = Math.max(.1, this.duration()), pos = Math.min(length, this.position());
    const format = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    this.track.max = String(length); this.track.value = String(pos); this.track.style.setProperty("--play-position", `${100 * pos / length}%`);
    if (this.event && this.eventIndex >= 0) { const [start, end] = this.bounds(); this.track.style.setProperty("--clip-start", `${100 * start / length}%`); this.track.style.setProperty("--clip-end", `${100 * end / length}%`); }
    this.clock.textContent = `${format(pos)} / ${format(length)}`;
    this.playButton.textContent = this.video.paused ? "Play" : "Pause"; this.muteButton.textContent = this.video.muted ? "Unmute" : "Mute";
    this.volume.value = String(this.video.volume);
    if (this.loopButton) { this.loopButton.textContent = this.loop ? "Loop on" : "Loop off"; this.loopButton.setAttribute("aria-pressed", String(this.loop)); }
    if (this.speed) this.speed.textContent = `${this.video.playbackRate}x`;
  }
  key(e) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.target.matches("input,button,select,textarea")) return;
    const key = e.key.toLowerCase(); let handled = true;
    if ([" ", "k"].includes(key)) this.togglePlay();
    else if (["arrowleft", "j"].includes(key)) this.seek(this.position() - (key === "j" ? 10 : 5));
    else if (["arrowright", "l"].includes(key)) this.seek(this.position() + (key === "l" ? 10 : 5));
    else if (key === "m") this.video.muted = !this.video.muted;
    else if (key === "home") this.seek(0);
    else if (key === "end") this.seek(this.duration());
    else if (key === "f") this.fullscreen();
    else if (["arrowup", "arrowdown"].includes(key)) { this.video.volume = Math.min(1, Math.max(0, this.video.volume + (key === "arrowup" ? .1 : -.1))); this.video.muted = false; }
    else handled = false;
    if (handled) e.preventDefault();
  }
  destroy() { this.feedback?.clear(this.messageId); this.disposed = true; clearInterval(this.timer); this.video.pause(); this.video.onloadedmetadata = null; this.video.removeAttribute("src"); this.video.load(); this.wrap.remove(); this.error.remove(); }
};

window.AuditPlayer.sequence = 0;
