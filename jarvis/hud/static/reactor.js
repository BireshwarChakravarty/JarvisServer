/* Arc reactor: layered, audio-reactive rings drawn on a 2D canvas. No dependencies. */
(function () {
  const PALETTE = {
    idle:      { main: [25, 230, 255], accent: [25, 230, 255], speed: 0.35, glow: 0.55 },
    listening: { main: [25, 230, 255], accent: [120, 255, 255], speed: 0.8, glow: 1.0 },
    transcribing: { main: [25, 230, 255], accent: [255, 177, 59], speed: 1.4, glow: 0.9 },
    thinking:  { main: [25, 230, 255], accent: [255, 177, 59], speed: 2.4, glow: 0.85 },
    working:   { main: [25, 230, 255], accent: [255, 177, 59], speed: 3.2, glow: 0.9 },
    speaking:  { main: [60, 240, 255], accent: [25, 230, 255], speed: 0.9, glow: 1.0 },
    booting:   { main: [25, 230, 255], accent: [255, 177, 59], speed: 4.0, glow: 0.6 },
    error:     { main: [255, 59, 92], accent: [255, 120, 120], speed: 1.0, glow: 0.8 },
    muted:     { main: [120, 150, 170], accent: [255, 59, 92], speed: 0.15, glow: 0.3 },
  };

  class Reactor {
    constructor(canvas) {
      this.c = canvas;
      this.ctx = canvas.getContext("2d");
      this.state = "booting";
      this.level = 0;          // smoothed audio level 0..1
      this.target = 0;
      this.t = 0;
      this.spin = 0;
      this.speed = 1;
      this.col = PALETTE.booting.main.slice();
      this.acc = PALETTE.booting.accent.slice();
      this.glow = 0.6;
      this.bars = new Float32Array(120);
      this.particles = [];
      this.flash = 0;
      this.resize();
      window.addEventListener("resize", () => this.resize());
      requestAnimationFrame((ts) => this.frame(ts));
    }
    resize() {
      const r = this.c.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.c.width = Math.max(10, r.width * dpr);
      this.c.height = Math.max(10, r.height * dpr);
      this.dpr = dpr;
    }
    setState(s) { if (PALETTE[s]) this.state = s; if (s === "listening") this.flash = 1; }
    setLevel(v) { this.target = Math.max(this.target, Math.min(1, v)); }
    pulse() { this.flash = 1; }

    rgba(c, a) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }

    frame(ts) {
      const dt = Math.min(0.05, (ts - (this.last || ts)) / 1000);
      this.last = ts;
      this.t += dt;
      const p = PALETTE[this.state] || PALETTE.idle;
      for (let i = 0; i < 3; i++) {
        this.col[i] += (p.main[i] - this.col[i]) * 0.06;
        this.acc[i] += (p.accent[i] - this.acc[i]) * 0.06;
      }
      this.speed += (p.speed - this.speed) * 0.04;
      this.glow += (p.glow - this.glow) * 0.05;
      this.spin += dt * this.speed;
      this.level += (this.target - this.level) * 0.25;
      this.target *= 0.86;
      this.flash *= 0.94;
      this.draw();
      requestAnimationFrame((t2) => this.frame(t2));
    }

    draw() {
      const { ctx } = this;
      const W = this.c.width, H = this.c.height;
      const cx = W / 2, cy = H / 2;
      const R = Math.min(W, H) * 0.46;
      const L = this.level, col = this.col, acc = this.acc, t = this.t, s = this.spin;
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(cx, cy);
      ctx.globalCompositeOperation = "lighter";

      // ── outer halo
      const halo = ctx.createRadialGradient(0, 0, R * 0.2, 0, 0, R * 1.05);
      halo.addColorStop(0, this.rgba(col, 0.10 * this.glow + L * 0.12));
      halo.addColorStop(0.6, this.rgba(col, 0.03 * this.glow));
      halo.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = halo;
      ctx.beginPath(); ctx.arc(0, 0, R * 1.05, 0, Math.PI * 2); ctx.fill();

      // ── tick ring (60 ticks), counter-rotating
      ctx.save();
      ctx.rotate(-s * 0.15);
      for (let i = 0; i < 120; i++) {
        const a = (i / 120) * Math.PI * 2;
        const major = i % 10 === 0;
        const r1 = R * 0.95, r2 = R * (major ? 0.89 : 0.925);
        ctx.strokeStyle = this.rgba(col, major ? 0.8 : 0.3);
        ctx.lineWidth = (major ? 2.2 : 1) * this.dpr;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();

      // ── sweeping arcs (HUD style)
      const arcs = [
        [0.84, 0.0, 1.1, 0.6, 3], [0.84, 3.3, 0.5, 0.6, 3], [0.80, 1.2, 2.1, -0.9, 1.4],
        [0.78, 4.4, 0.9, -0.9, 1.4], [0.72, 0.4, 0.7, 1.6, 2.2], [0.72, 2.6, 1.6, 1.6, 2.2],
      ];
      for (const [rr, start, len, sp, w] of arcs) {
        ctx.strokeStyle = this.rgba(sp > 1 ? acc : col, 0.55 + 0.3 * this.glow);
        ctx.lineWidth = w * this.dpr;
        ctx.beginPath();
        const a0 = start + s * sp;
        ctx.arc(0, 0, R * rr, a0, a0 + len);
        ctx.stroke();
      }

      // ── audio bars ring
      const n = this.bars.length;
      for (let i = 0; i < n; i++) {
        const wobble = 0.5 + 0.5 * Math.sin(i * 0.37 + t * 3.1) * Math.sin(i * 0.13 - t * 1.7);
        const want = L * (0.35 + 0.65 * wobble) + 0.02 + (this.state === "thinking" || this.state === "working"
          ? 0.12 * (0.5 + 0.5 * Math.sin(i * 0.5 - t * 9)) : 0);
        this.bars[i] += (want - this.bars[i]) * 0.3;
      }
      ctx.save();
      ctx.rotate(s * 0.05);
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        const r1 = R * 0.6, r2 = r1 + R * (0.015 + this.bars[i] * 0.2);
        ctx.strokeStyle = this.rgba(col, 0.35 + this.bars[i] * 0.9);
        ctx.lineWidth = 2.4 * this.dpr;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();

      // ── segmented ring (the reactor's coils)
      ctx.save();
      ctx.rotate(s * 0.4);
      const segs = 10;
      for (let i = 0; i < segs; i++) {
        const a0 = (i / segs) * Math.PI * 2 + 0.06, a1 = ((i + 1) / segs) * Math.PI * 2 - 0.06;
        ctx.fillStyle = this.rgba(col, 0.16 + 0.22 * this.glow + L * 0.3);
        ctx.beginPath();
        ctx.arc(0, 0, R * 0.53, a0, a1);
        ctx.arc(0, 0, R * 0.42, a1, a0, true);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = this.rgba(col, 0.7);
        ctx.lineWidth = 1 * this.dpr;
        ctx.stroke();
      }
      ctx.restore();

      // ── inner triangle (classic mk-II reactor motif)
      ctx.save();
      ctx.rotate(-s * 0.25);
      ctx.strokeStyle = this.rgba(acc, 0.35 + 0.4 * this.glow);
      ctx.lineWidth = 2 * this.dpr;
      ctx.beginPath();
      for (let i = 0; i < 3; i++) {
        const a = (i / 3) * Math.PI * 2 - Math.PI / 2;
        const x = Math.cos(a) * R * 0.36, y = Math.sin(a) * R * 0.36;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.closePath();
      ctx.stroke();
      ctx.restore();

      // ── core
      const coreR = R * (0.2 + L * 0.07 + this.flash * 0.04);
      const core = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR * 1.8);
      core.addColorStop(0, `rgba(255,255,255,${0.85 * this.glow + 0.15})`);
      core.addColorStop(0.25, this.rgba(col, 0.9 * this.glow + 0.1));
      core.addColorStop(0.6, this.rgba(col, 0.25 * this.glow + L * 0.3));
      core.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = core;
      ctx.beginPath(); ctx.arc(0, 0, coreR * 1.8, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = this.rgba(col, 0.9);
      ctx.lineWidth = 1.5 * this.dpr;
      ctx.beginPath(); ctx.arc(0, 0, R * 0.28, 0, Math.PI * 2); ctx.stroke();

      // ── particles drifting outward when there's sound
      if (Math.random() < 0.15 + L * 0.9) {
        const a = Math.random() * Math.PI * 2;
        this.particles.push({ a, r: R * 0.3, v: R * (0.12 + Math.random() * 0.35 + L * 0.5), life: 1 });
      }
      for (const pt of this.particles) {
        pt.r += pt.v * 0.016; pt.life -= 0.012;
        ctx.fillStyle = this.rgba(col, Math.max(0, pt.life) * 0.7);
        ctx.beginPath(); ctx.arc(Math.cos(pt.a) * pt.r, Math.sin(pt.a) * pt.r, 1.4 * this.dpr, 0, Math.PI * 2); ctx.fill();
      }
      this.particles = this.particles.filter((pt) => pt.life > 0 && pt.r < R * 1.1);

      ctx.restore();
    }
  }
  window.Reactor = Reactor;
})();
