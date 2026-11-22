/* Mini JARVIS: a small capsule while he's active. State, the line he's saying, your words while he
   listens. Sentences become one-line caption blocks (~34 characters), paced with his voice or at
   reading speed: nothing is cut off with an ellipsis. It fades when he goes quiet. */
(() => {
  const $ = (id) => document.getElementById(id);
  const reactor = new Reactor($("core-gl"), $("core-ui"), { compact: true });
  const LABEL = { idle: "Standby", listening: "Listening", transcribing: "Hearing you", thinking: "Thinking",
    working: "On it", speaking: "Speaking", error: "Fault", muted: "Muted", booting: "Starting" };
  let state = "idle", agenda = [], queue = [], timer = null, quietTimer = null;

  const ACTIVE = ["listening", "transcribing", "thinking", "working", "speaking", "error"];
  let fadeTimer = null;
  function setState(s) {
    state = s; document.body.dataset.state = s; reactor.setState(s);
    $("st").textContent = LABEL[s] || s;
    clearTimeout(fadeTimer);
    if (ACTIVE.includes(s)) document.body.classList.remove("quiet");
    else fadeTimer = setTimeout(() => document.body.classList.add("quiet"), 2800);   // the window goes at 3.5 s
    if (s === "idle") { clearTimeout(quietTimer); quietTimer = setTimeout(idleLine, 6000); }
  }
  function wake() { clearTimeout(fadeTimer); document.body.classList.remove("quiet"); }

  function show(text, cls) {
    const el = $("line");
    el.classList.add("swap");
    setTimeout(() => { el.textContent = text; el.className = "line " + (cls || ""); }, 180);
  }

  // split a sentence into blocks that fit two lines (≈60 chars), at punctuation, then words
  // Split what he says into pieces that each fit the capsule's one line, measured in the real font at
  // the real width (counting characters let a wide piece wrap onto a second, clipped line).
  const measure = document.createElement("canvas").getContext("2d");
  function fits(text) {
    const el = $("line");
    const room = el && el.clientWidth ? el.clientWidth - 2 : 160;
    const cs = el ? getComputedStyle(el) : null;
    measure.font = cs ? `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}` : "400 11.5px sans-serif";
    return measure.measureText(text).width <= room;
  }
  // your words in quotes; if they're long, the latest ones (like live captions), never a cut-off line
  function latest(text) {
    const words = String(text || "").trim().split(/\s+/);
    if (fits(`“${words.join(" ")}”`)) return `“${words.join(" ")}”`;
    let tail = [];
    for (let i = words.length - 1; i >= 0; i--) {
      if (!fits(`“…${[words[i], ...tail].join(" ")}”`)) break;
      tail.unshift(words[i]);
    }
    return `“…${tail.join(" ")}”`;
  }
  function blocks(text) {
    const out = [];
    const parts = text.split(/(?<=[,;:—.!?])\s+/);
    let cur = "";
    const push = () => { if (cur) out.push(cur); cur = ""; };
    for (const p of parts) {
      const joined = (cur + " " + p).trim();
      if (fits(joined)) { cur = joined; continue; }
      push();
      if (fits(p)) { cur = p; continue; }
      for (const w of p.split(/\s+/)) {
        const next = (cur + " " + w).trim();
        if (cur && !fits(next)) { push(); cur = w; } else cur = next;
      }
    }
    push();
    return out;
  }
  // with the phrase's real audio duration, each block is shown while its words are spoken
  function say(text, duration) {
    const bs = blocks(text);
    if (duration) {
      queue = []; clearTimeout(timer); timer = null;
      const total = bs.reduce((n, b) => n + b.length, 0) || 1;
      let at = 0;
      bs.forEach((b) => { setTimeout(() => show(b), at); at += duration * 1000 * (b.length / total); });
      return;
    }
    queue.push(...bs);
    if (!timer) next();
  }
  function next() {
    const b = queue.shift();
    if (!b) { timer = null; return; }
    show(b);
    timer = setTimeout(next, Math.max(1300, (b.length / 15) * 1000));
  }

  window.addEventListener("jarvis-clock", () => idleLine());
  function idleLine() {
    if (state !== "idle") return;
    const now = Date.now();
    const e = agenda.filter((x) => x.start && !x.all_day && new Date(x.end || x.start).getTime() > now)
      .sort((a, b) => new Date(a.start) - new Date(b.start))[0];
    if (!e) return show("Say “Hey Jarvis”", "quiet");
    const s = new Date(e.start).getTime();
    const when = s <= now ? "Now" : JarvisClock.time(s);
    show(`${when} · ${e.title}`, "quiet");
  }

  function handle(e) {
    if ((e.type === "hello" || e.type === "theme") && e.theme && window.JarvisTheme) JarvisTheme.apply(e.theme);
    if ((e.type === "hello" || e.type === "prefs") && "clock24" in e) JarvisClock.set(e.clock24);
    switch (e.type) {
      case "state": setState(e.state); break;
      case "level": if (e.source === "tts" || state === "listening") reactor.setLevel(e.value, e.source); break;
      case "wake": wake(); reactor.pulse(); setState("listening"); break;
      case "transcript": if (!e.replay) { queue = []; clearTimeout(timer); timer = null; show(latest(e.text), "you"); } break;
      case "speaking": if (!e.replay) say(e.text, e.duration); break;
      case "agenda": agenda = e.events || []; if (state === "idle") idleLine(); break;
      case "announce": if (!e.replay) { wake(); say(e.text); if (!ACTIVE.includes(state)) setState(state); } break;
      case "mic": if (e.muted) setState("muted"); break;
    }
  }

  // click (not a drag) brings the full HUD back
  let down = null;
  document.addEventListener("mousedown", (ev) => (down = [ev.screenX, ev.screenY]));
  document.addEventListener("mouseup", (ev) => {
    if (down && Math.hypot(ev.screenX - down[0], ev.screenY - down[1]) < 4) fetch("/api/app/show", { method: "POST" }).catch(() => {});
    down = null;
  });

  function connect() {
    if (new URLSearchParams(location.search).has("demo")) { window.__handle = handle; setState("idle"); return; }
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (err) { console.error(err); } };
    ws.onclose = () => setTimeout(connect, 1500);
  }
  connect();
  setState("idle");
})();
