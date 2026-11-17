/* JARVIS on-screen guidance: a light flies to the thing to click, lassos it, and says why.
 * The Mac app calls guide.show({x, y, w, h, caption, step, total, style, kind}) with the target's
 * rectangle in this window's coordinates, then guide.done(ok) once you've clicked (or given up). */
(() => {
  const $ = (id) => document.getElementById(id);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const orb = $("orb"), lasso = $("lasso"), glow = $("lasso-glow"), trail = $("trail"), card = $("card");
  let from = null, raf = 0, timers = [];

  const ease = (t) => 1 - Math.pow(1 - t, 3);
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));
  function clear() {
    cancelAnimationFrame(raf); timers.forEach(clearTimeout); timers = [];
    document.body.className = ""; card.classList.remove("in");
  }

  // a loop drawn by hand around the target: a padded superellipse with a little wobble, starting
  // where the light arrives and overshooting past the start, like a lasso pulled closed
  function loop(r, startAngle) {
    const pad = 9 + Math.min(14, Math.min(r.w, r.h) * 0.12);
    const cx = r.x + r.w / 2, cy = r.y + r.h / 2, ax = r.w / 2 + pad, ay = r.h / 2 + pad;
    const pts = [], n = 120, turns = 1.12;
    for (let i = 0; i <= n; i++) {
      const t = startAngle + (i / n) * turns * Math.PI * 2;
      const c = Math.cos(t), s = Math.sin(t);
      const k = 2.6;                                   // superellipse: hugs rectangles, still round
      const ex = Math.sign(c) * Math.pow(Math.abs(c), 2 / k), ey = Math.sign(s) * Math.pow(Math.abs(s), 2 / k);
      const wob = 1 + 0.035 * Math.sin(t * 3 + startAngle) + (i / n) * 0.05;
      pts.push([cx + ex * ax * wob, cy + ey * ay * wob]);
    }
    return pts;
  }
  const d = (pts) => "M" + pts.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L");

  function place(r) {
    card.style.left = "0px"; card.style.top = "0px";
    const cw = card.offsetWidth, ch = card.offsetHeight, W = innerWidth, H = innerHeight, gap = 22;
    const options = [
      [r.x + r.w + gap, r.y + r.h / 2 - ch / 2],           // right
      [r.x - gap - cw, r.y + r.h / 2 - ch / 2],            // left
      [r.x + r.w / 2 - cw / 2, r.y + r.h + gap],           // below
      [r.x + r.w / 2 - cw / 2, r.y - gap - ch],            // above
    ];
    const fits = ([x, y]) => x >= 8 && y >= 8 && x + cw <= W - 8 && y + ch <= H - 8;
    let [x, y] = options.find(fits) || options[2];
    x = Math.max(8, Math.min(W - cw - 8, x)); y = Math.max(8, Math.min(H - ch - 8, y));
    card.style.left = x + "px"; card.style.top = y + "px";
  }

  function show(p) {
    clear();
    const r = { x: p.x, y: p.y, w: Math.max(4, p.w), h: Math.max(4, p.h) };
    const hint = p.style === "hint";
    document.body.classList.add("on");
    if (hint) document.body.classList.add("hint");
    $("k-step").textContent = p.total > 1 ? `Step ${p.step} of ${p.total}` : "";
    $("k-kind").textContent = p.kind && !hint ? p.kind : "";
    $("say").textContent = p.caption || "";
    card.hidden = !p.caption;

    const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
    const start = from || [innerWidth / 2, innerHeight - 60];
    const ang = Math.atan2(start[1] - cy, start[0] - cx);
    const path = loop(r, ang);
    const end = path[0];
    lasso.setAttribute("d", d(path)); glow.setAttribute("d", d(path));
    const len = lasso.getTotalLength();
    [lasso, glow].forEach((el) => { el.style.strokeDasharray = len; el.style.strokeDashoffset = len; });

    const fly = reduce ? 0 : hint ? 320 : 620, draw = reduce ? 0 : hint ? 380 : 650;
    // the curve the light travels: bowed to one side so it reads as flight, not a slide
    const mx = (start[0] + end[0]) / 2, my = (start[1] + end[1]) / 2;
    const nx = -(end[1] - start[1]), ny = end[0] - start[0], nl = Math.hypot(nx, ny) || 1;
    const bow = Math.min(160, Math.hypot(end[0] - start[0], end[1] - start[1]) * 0.25);
    const ctrl = [mx + (nx / nl) * bow, my + (ny / nl) * bow];
    const at = (t) => [(1 - t) ** 2 * start[0] + 2 * (1 - t) * t * ctrl[0] + t * t * end[0],
                       (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * ctrl[1] + t * t * end[1]];
    const t0 = performance.now(), tail = [];
    function frame(now) {
      const e = now - t0;
      if (e < fly) {
        const pt = at(ease(e / fly));
        orb.setAttribute("cx", pt[0]); orb.setAttribute("cy", pt[1]);
        tail.push(pt); if (tail.length > 14) tail.shift();
        trail.setAttribute("d", tail.length > 1 ? d(tail) : "");
      } else {
        const k = Math.min(1, (e - fly) / Math.max(1, draw));
        const off = len * (1 - ease(k));
        lasso.style.strokeDashoffset = off; glow.style.strokeDashoffset = off;
        const pt = lasso.getPointAtLength(len * ease(k));
        orb.setAttribute("cx", pt.x); orb.setAttribute("cy", pt.y);
        if (tail.length) { tail.shift(); trail.setAttribute("d", tail.length > 1 ? d(tail) : ""); }
        if (k >= 1) {
          document.body.classList.add("pulse");
          orb.setAttribute("r", hint ? 8 : 10);
          return;
        }
      }
      raf = requestAnimationFrame(frame);
    }
    orb.setAttribute("r", hint ? 9 : 14);
    raf = requestAnimationFrame(frame);
    if (p.caption) { place(r); later(() => card.classList.add("in"), reduce ? 0 : fly * 0.6); }
    from = [cx, cy];
  }

  function done(ok) {
    if (ok) document.body.classList.add("ok");
    document.body.classList.add("leaving");
    later(hide, 800);
  }
  function hide() { clear(); trail.setAttribute("d", ""); lasso.setAttribute("d", ""); glow.setAttribute("d", ""); card.hidden = true; }
  function reset() { from = null; hide(); }

  window.guide = { show, done, hide, reset };

  // ?demo — a static frame for screenshots and the audit
  const q = new URLSearchParams(location.search);
  if (q.has("demo")) {
    document.body.style.setProperty("background", q.has("light") ? "#f4f5f7" : "#1d2733", "important");
    const target = { x: innerWidth * 0.55, y: innerHeight * 0.32, w: 92, h: 30 };
    const fake = document.createElement("div");
    Object.assign(fake.style, { position: "fixed", left: target.x + "px", top: target.y + "px", width: target.w + "px",
      height: target.h + "px", borderRadius: "6px", background: "#3a4757", color: "#fff", font: "13px system-ui",
      display: "grid", placeItems: "center" });
    fake.textContent = "AutoSum"; document.body.prepend(fake);
    from = [innerWidth * 0.2, innerHeight * 0.8];
    show({ ...target, caption: q.has("hint") ? "AutoSum" : "Click AutoSum to total the column. It adds up the numbers above the selected cell.",
      step: 1, total: 2, style: q.has("hint") ? "hint" : "full", kind: "button" });
  }
})();
