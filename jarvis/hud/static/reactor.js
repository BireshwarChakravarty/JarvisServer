/* ─────────────────────────────────────────────────────────────────────────────
   JARVIS core: a WebGL liquid-light arc reactor with a 2D instrument overlay.

   Every ring means something (Hansen's rule: "everything had a purpose"):
     • core       – JARVIS himself. Breathes when idle, ripples with sound,
                    swirls while thinking. Colour = state.
     • coil ring  – the reactor's ten coils. Spin up while he thinks/acts;
                    an indeterminate sweep shows work in progress.
     • voice ring – live audio. Gold = you speaking, ice-blue = JARVIS speaking.
     • chrono ring– a 12-hour dial around "now": hour ticks, a needle for the
                    current time, and today's meetings drawn as arcs.
   ───────────────────────────────────────────────────────────────────────────── */
(function () {
  const STATES = {
    //            core colour A       core colour B        swirl  glow  ring colour
    idle:       { a: [0.62, 0.96, 1.0], b: [0.05, 0.42, 0.95], swirl: 0.25, glow: 0.75, ring: [140, 236, 255] },
    listening:  { a: [1.0, 0.93, 0.78], b: [0.10, 0.58, 1.0],  swirl: 0.45, glow: 1.0,  ring: [255, 196, 108] },
    transcribing:{ a: [0.85, 0.95, 1.0], b: [0.30, 0.42, 1.0], swirl: 0.9,  glow: 0.9,  ring: [180, 200, 255] },
    thinking:   { a: [0.80, 0.90, 1.0], b: [0.36, 0.30, 0.98], swirl: 1.6,  glow: 0.95, ring: [168, 176, 255] },
    working:    { a: [0.95, 0.88, 1.0], b: [0.40, 0.26, 0.95], swirl: 2.0,  glow: 1.0,  ring: [190, 170, 255] },
    speaking:   { a: [0.74, 0.98, 1.0], b: [0.02, 0.55, 1.0],  swirl: 0.6,  glow: 1.05, ring: [150, 240, 255] },
    booting:    { a: [0.70, 0.95, 1.0], b: [0.05, 0.42, 0.95], swirl: 1.2,  glow: 0.6,  ring: [140, 236, 255] },
    error:      { a: [1.0, 0.75, 0.72], b: [0.85, 0.12, 0.22], swirl: 0.8,  glow: 0.9,  ring: [255, 120, 120] },
    muted:      { a: [0.55, 0.62, 0.70], b: [0.12, 0.18, 0.28], swirl: 0.1,  glow: 0.35, ring: [120, 140, 160] },
  };

  const UI_SCALE = 1.6;   // #core-ui extends 30% beyond the core on each side (see hud.css)

  const VERT = `
    attribute vec2 p;
    varying vec2 v;
    void main() { v = p; gl_Position = vec4(p, 0.0, 1.0); }`;

  const FRAG = `
    precision highp float;
    varying vec2 v;
    uniform vec2  uRes;
    uniform float uTime, uLevel, uSwirl, uGlow, uBirth, uRadius;
    uniform vec3  uA, uB;

    float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
    float noise(vec2 p) {
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
    }
    float fbm(vec2 p) {
      float s = 0.0, a = 0.5;
      for (int i = 0; i < 5; i++) { s += a * noise(p); p = p * 2.03 + 11.7; a *= 0.5; }
      return s;
    }

    void main() {
      vec2 uv = v * vec2(uRes.x / uRes.y, 1.0) / uRadius;
      float t = uTime;
      float r0 = length(uv);
      vec2 dir = uv / max(r0, 1e-4);

      // liquid edge: periodic in angle (no seam), nearly still at rest, alive with sound
      float wob = (noise(dir * 1.9 + vec2(t * 0.45, -t * 0.38)) - 0.5) * (0.018 + 0.15 * uLevel)
                + (noise(dir * 4.3 - vec2(t * 0.9, t * 0.7)) - 0.5) * 0.06 * uLevel;
      float breathe = 1.0 + 0.016 * sin(t * 1.3);
      float R = (0.62 + 0.05 * uLevel) * breathe * uBirth;
      float r = r0 / max(R * (1.0 + wob), 0.0001);

      // plasma: rotating, domain-warped fbm
      float rot = t * 0.22 * uSwirl;
      mat2 m = mat2(cos(rot), -sin(rot), sin(rot), cos(rot));
      vec2 q = m * uv * 2.1;
      vec2 w = vec2(fbm(q + t * 0.12 * uSwirl), fbm(q + 5.2 - t * 0.1 * uSwirl));
      float plasma = fbm(q + 2.2 * w + t * 0.04);
      float ridge = 1.0 - abs(plasma * 2.0 - 1.0);           // bright filaments along fbm ridges
      float fil = pow(ridge, 7.0);

      vec3 col = vec3(0.0);
      float alpha = 0.0;
      if (r < 1.0) {
        float z = sqrt(1.0 - r * r);
        float fres = pow(1.0 - z, 3.0);
        vec3 deep = uB * (0.12 + 0.35 * plasma);
        vec3 light = mix(uB, uA, 0.6);
        float core = exp(-r * r * 11.0) * (0.55 + 0.45 * uLevel);
        float corona = exp(-r * r * 3.0) * 0.35;
        col = deep + light * fil * (0.55 + 0.6 * z) + uA * fres * 1.35 + mix(uA, vec3(1.0), 0.6) * (core + corona);
        alpha = smoothstep(1.0, 0.985, r);
      }
      // halo and sound ripples outside the body
      float d = max(r - 1.0, 0.0);
      float halo = exp(-d * 5.5) * 0.42 * uGlow;
      float ripple = uLevel * 0.5 * exp(-d * 2.8) * pow(0.5 + 0.5 * sin(d * 28.0 - t * 7.0), 8.0);
      vec3 glowc = mix(uB, uA, 0.45);
      col += glowc * (halo + ripple) * (1.0 - alpha);
      alpha = max(alpha, clamp(halo + ripple, 0.0, 1.0));
      col *= 0.15 + 0.9 * uGlow;
      gl_FragColor = vec4(col, alpha);
    }`;

  class Reactor {
    constructor(glCanvas, uiCanvas) {
      this.gc = glCanvas; this.uc = uiCanvas;
      this.ui = uiCanvas.getContext("2d");
      this.state = "booting";
      this.level = 0; this.target = 0; this.voiceSource = "tts";
      this.t = 0; this.spin = 0; this.sweep = 0; this.birth = 0; this.ringsOn = 0;
      this.cur = this.clone(STATES.booting);
      this.events = [];
      this.bars = new Float32Array(160);
      this.reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.initGL();
      this.resize();
      new ResizeObserver(() => this.resize()).observe(this.gc.parentElement);
      requestAnimationFrame((ts) => this.frame(ts));
    }
    clone(s) { return { a: s.a.slice(), b: s.b.slice(), swirl: s.swirl, glow: s.glow, ring: s.ring.slice() }; }

    initGL() {
      const gl = this.gc.getContext("webgl", { premultipliedAlpha: false, alpha: true, antialias: true });
      if (!gl) { this.gl = null; return; }
      const sh = (type, src) => {
        const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
        return s;
      };
      try {
        const prog = gl.createProgram();
        gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
        gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
        gl.linkProgram(prog);
        gl.useProgram(prog);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "p");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        this.u = {};
        ["uRes", "uTime", "uLevel", "uSwirl", "uGlow", "uBirth", "uRadius", "uA", "uB"].forEach((n) => (this.u[n] = gl.getUniformLocation(prog, n)));
        this.gl = gl;
      } catch (e) { console.warn("WebGL core unavailable, using 2D fallback", e); this.gl = null; }
    }

    resize() {
      const r = this.gc.parentElement.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.gc.width = Math.max(10, r.width * dpr); this.gc.height = Math.max(10, r.height * dpr);
      this.uc.width = Math.max(10, r.width * UI_SCALE * dpr); this.uc.height = Math.max(10, r.height * UI_SCALE * dpr);
      this.dpr = dpr;
      if (this.gl) this.gl.viewport(0, 0, this.gc.width, this.gc.height);
    }

    setState(s) { if (STATES[s]) this.state = s; }
    setLevel(v, source) { this.target = Math.max(this.target, Math.min(1, v)); if (source) this.voiceSource = source; }
    setEvents(events) { this.events = events || []; }
    pulse() { this.target = Math.max(this.target, 0.8); }

    frame(ts) {
      const dt = Math.min(0.05, (ts - (this.last || ts)) / 1000);
      this.last = ts;
      const k = this.reduced ? 0.35 : 1;
      this.t += dt * k;
      const want = STATES[this.state] || STATES.idle;
      const ease = 1 - Math.pow(0.02, dt);          // frame-rate independent smoothing
      for (let i = 0; i < 3; i++) {
        this.cur.a[i] += (want.a[i] - this.cur.a[i]) * ease;
        this.cur.b[i] += (want.b[i] - this.cur.b[i]) * ease;
        this.cur.ring[i] += (want.ring[i] - this.cur.ring[i]) * ease;
      }
      this.cur.swirl += (want.swirl - this.cur.swirl) * ease;
      this.cur.glow += (want.glow - this.cur.glow) * ease;
      this.level += (this.target - this.level) * Math.min(1, dt * 14);
      this.target *= Math.pow(0.06, dt);
      this.birth = Math.min(1, this.birth + dt * 0.7);
      this.ringsOn = Math.min(1, this.ringsOn + dt * (this.birth > 0.6 ? 0.55 : 0));
      const busy = this.state === "thinking" || this.state === "working" || this.state === "transcribing" || this.state === "booting";
      this.spin += dt * k * (busy ? 1.4 : 0.12);
      this.sweep += dt * k * (busy ? 3.2 : 0);
      this.drawCore();
      this.drawRings(busy);
      requestAnimationFrame((t2) => this.frame(t2));
    }

    get radiusFrac() { return 0.27; }   // core radius as fraction of the shorter side

    drawCore() {
      const birth = 1 - Math.pow(1 - this.birth, 3);
      if (this.gl) {
        const gl = this.gl, u = this.u;
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.uniform2f(u.uRes, this.gc.width, this.gc.height);
        gl.uniform1f(u.uTime, this.t);
        gl.uniform1f(u.uLevel, this.level);
        gl.uniform1f(u.uSwirl, this.cur.swirl);
        gl.uniform1f(u.uGlow, this.cur.glow);
        gl.uniform1f(u.uBirth, birth);
        gl.uniform1f(u.uRadius, this.radiusFrac * 2 / 0.62 * Math.min(1, this.gc.width / this.gc.height));
        gl.uniform3fv(u.uA, this.cur.a);
        gl.uniform3fv(u.uB, this.cur.b);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        return;
      }
      // 2D fallback
      const c = this.gc.getContext("2d"); if (!c) return;
      const W = this.gc.width, H = this.gc.height, R = Math.min(W, H) * this.radiusFrac * birth * (1 + this.level * 0.08);
      c.clearRect(0, 0, W, H);
      const g = c.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, R * 1.8);
      const a = this.cur.a.map((x) => (x * 255) | 0), b = this.cur.b.map((x) => (x * 255) | 0);
      g.addColorStop(0, "rgba(255,255,255,0.95)"); g.addColorStop(0.25, `rgba(${a},0.9)`);
      g.addColorStop(0.55, `rgba(${b},0.45)`); g.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = g; c.beginPath(); c.arc(W / 2, H / 2, R * 1.8, 0, Math.PI * 2); c.fill();
    }

    rgba(c, a) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }

    drawRings(busy) {
      const ctx = this.ui, W = this.uc.width, H = this.uc.height, dpr = this.dpr;
      const cx = W / 2, cy = H / 2, base = (Math.min(W, H) / UI_SCALE) * this.radiusFrac;
      const on = this.ringsOn, rc = this.cur.ring;
      ctx.clearRect(0, 0, W, H);
      if (on <= 0) return;
      ctx.save(); ctx.translate(cx, cy); ctx.lineCap = "round";
      const TAU = Math.PI * 2, top = -Math.PI / 2;

      // ── coil ring: ten reactor coils
      const rCoil = base * 1.17;
      ctx.save(); ctx.rotate(this.spin * 0.6);
      for (let i = 0; i < 10; i++) {
        const a0 = (i / 10) * TAU + 0.05, a1 = ((i + 1) / 10) * TAU - 0.05;
        ctx.strokeStyle = this.rgba(rc, (0.22 + 0.25 * this.cur.glow) * on);
        ctx.lineWidth = 2.2 * dpr;
        ctx.beginPath(); ctx.arc(0, 0, rCoil, a0, a0 + (a1 - a0) * on); ctx.stroke();
      }
      ctx.restore();
      if (busy) {  // indeterminate progress sweep
        const s = this.sweep;
        ctx.strokeStyle = this.rgba(rc, 0.9 * on); ctx.lineWidth = 2.2 * dpr;
        ctx.beginPath(); ctx.arc(0, 0, rCoil, s, s + 0.9 + 0.5 * Math.sin(s * 0.7)); ctx.stroke();
      }

      // ── voice ring: radial hairlines driven by live audio
      const rVoice = base * 1.30, n = this.bars.length;
      const voiceCol = this.state === "listening" || this.voiceSource === "mic" && this.state !== "speaking" ? [255, 196, 108] : rc;
      for (let i = 0; i < n; i++) {
        const wob = 0.5 + 0.5 * Math.sin(i * 0.41 + this.t * 3.3) * Math.sin(i * 0.17 - this.t * 2.1);
        const sym = Math.abs(Math.sin((i / n) * Math.PI * 4 + this.t * 0.5));
        const want = this.level * (0.25 + 0.75 * wob) * (0.6 + 0.4 * sym);
        this.bars[i] += (want - this.bars[i]) * 0.25;
      }
      for (let i = 0; i < n; i++) {
        const a = (i / n) * TAU + top;
        const len = base * (0.012 + this.bars[i] * 0.16);
        ctx.strokeStyle = this.rgba(voiceCol, (0.16 + this.bars[i] * 1.2) * on);
        ctx.lineWidth = 1.4 * dpr;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * rVoice, Math.sin(a) * rVoice);
        ctx.lineTo(Math.cos(a) * (rVoice + len), Math.sin(a) * (rVoice + len));
        ctx.stroke();
      }

      // ── chronometer ring: the next 12 hours around "now"
      const rChrono = base * 1.62;
      const now = new Date();
      const hoursNow = now.getHours() % 12 + now.getMinutes() / 60 + now.getSeconds() / 3600;
      const angOf = (h) => top + (h / 12) * TAU;
      ctx.strokeStyle = this.rgba(rc, 0.10 * on); ctx.lineWidth = 1 * dpr;
      ctx.beginPath(); ctx.arc(0, 0, rChrono, top, top + TAU * on); ctx.stroke();
      for (let i = 0; i < 60; i++) {
        if (i / 60 > on) break;
        const a = top + (i / 60) * TAU, major = i % 5 === 0;
        const r1 = rChrono - (major ? 7 : 3) * dpr, r2 = rChrono + (major ? 7 : 0) * dpr;
        ctx.strokeStyle = this.rgba(rc, (major ? 0.45 : 0.14) * on);
        ctx.lineWidth = (major ? 1.4 : 1) * dpr;
        ctx.beginPath(); ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1); ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2); ctx.stroke();
      }
      // today's events as arcs (only the next 12 hours, so the dial never wraps onto itself)
      const nowMs = now.getTime();
      let nextLabel = null;
      for (const e of this.events) {
        if (!e.start || e.all_day) continue;
        const s = new Date(e.start).getTime(), f = e.end ? new Date(e.end).getTime() : s + 30 * 60e3;
        if (f < nowMs || s > nowMs + 12 * 3600e3) continue;
        const hs = hoursNow + (Math.max(s, nowMs) - nowMs) / 3600e3, hf = hoursNow + (Math.min(f, nowMs + 12 * 3600e3) - nowMs) / 3600e3;
        const live = s <= nowMs;
        const col = live ? [255, 196, 108] : rc;
        ctx.strokeStyle = this.rgba(col, (live ? 0.95 : 0.6) * on);
        ctx.lineWidth = 3.2 * dpr;
        ctx.beginPath(); ctx.arc(0, 0, rChrono + 13 * dpr, angOf(hs), Math.max(angOf(hs) + 0.02, angOf(hf))); ctx.stroke();
        if (!nextLabel || s < nextLabel.s) nextLabel = { s, a: angOf(hs), e, live };
      }
      // the "now" needle
      const an = angOf(hoursNow);
      ctx.strokeStyle = this.rgba([255, 255, 255], 0.85 * on); ctx.lineWidth = 1.6 * dpr;
      ctx.beginPath(); ctx.moveTo(Math.cos(an) * (rChrono - 12 * dpr), Math.sin(an) * (rChrono - 12 * dpr));
      ctx.lineTo(Math.cos(an) * (rChrono + 20 * dpr), Math.sin(an) * (rChrono + 20 * dpr)); ctx.stroke();
      ctx.fillStyle = this.rgba([255, 255, 255], on);
      ctx.beginPath(); ctx.arc(Math.cos(an) * (rChrono + 20 * dpr), Math.sin(an) * (rChrono + 20 * dpr), 2.6 * dpr, 0, TAU); ctx.fill();
      // label the next meeting where it sits on the dial
      if (nextLabel && on > 0.95) {
        const a = nextLabel.a, rl = rChrono + 30 * dpr;
        const x = Math.cos(a) * rl, y = Math.sin(a) * rl;
        const when = new Date(nextLabel.s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        ctx.font = `500 ${11 * dpr}px "Geist Mono", ui-monospace, monospace`;
        ctx.textAlign = Math.cos(a) >= 0 ? "left" : "right";
        ctx.textBaseline = "middle";
        ctx.fillStyle = this.rgba(nextLabel.live ? [255, 196, 108] : [220, 240, 255], 0.85);
        const title = nextLabel.e.title.length > 22 ? nextLabel.e.title.slice(0, 21) + "…" : nextLabel.e.title;
        ctx.fillText(`${nextLabel.live ? "NOW" : when.toUpperCase()} · ${title}`, x, y);
      }
      ctx.restore();
    }
  }
  window.Reactor = Reactor;
})();
