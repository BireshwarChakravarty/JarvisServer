/* Mini JARVIS: state, the line he's saying, your words while he listens, the next meeting when idle.
   Long sentences become caption blocks of at most two lines, paced at reading speed (~15 chars/s):
   nothing is ever cut off with an ellipsis. */
(() => {
  const $ = (id) => document.getElementById(id);
  const reactor = new Reactor($("core-gl"), $("core-ui"), { compact: true });
  const LABEL = { idle: "Standby", listening: "Listening", transcribing: "Hearing you", thinking: "Thinking",
    working: "On it", speaking: "Speaking", error: "Fault", muted: "Muted", booting: "Starting" };
  let state = "idle", agenda = [], queue = [], timer = null, quietTimer = null;

  function setState(s) {
    state = s; document.body.dataset.state = s; reactor.setState(s);
    $("st").textContent = LABEL[s] || s;
    if (s === "idle") { clearTimeout(quietTimer); quietTimer = setTimeout(idleLine, 6000); }
  }

  function show(text, cls) {
    const el = $("line");
    el.classList.add("swap");
    setTimeout(() => { el.textContent = text; el.className = "line " + (cls || ""); }, 180);
  }

  // split a sentence into blocks that fit two lines (≈60 chars), at punctuation, then words
  function blocks(text, max = 60) {
    const out = [];
    const parts = text.split(/(?<=[,;:—])\s+/);
    let cur = "";
    for (const p of parts) {
      if ((cur + " " + p).trim().length <= max) { cur = (cur + " " + p).trim(); continue; }
      if (cur) out.push(cur);
      if (p.length <= max) { cur = p; continue; }
      cur = "";
      for (const w of p.split(/\s+/)) {
        if ((cur + " " + w).trim().length > max && cur) { out.push(cur); cur = w; } else cur = (cur + " " + w).trim();
      }
    }
    if (cur) out.push(cur);
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

  function idleLine() {
    if (state !== "idle") return;
    const now = Date.now();
    const e = agenda.filter((x) => x.start && !x.all_day && new Date(x.end || x.start).getTime() > now)
      .sort((a, b) => new Date(a.start) - new Date(b.start))[0];
    if (!e) return show("Say “Hey Jarvis”", "quiet");
    const s = new Date(e.start).getTime();
    const when = s <= now ? "Now" : new Date(s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    show(`${when} · ${e.title}`, "quiet");
  }

  function handle(e) {
    switch (e.type) {
      case "state": setState(e.state); break;
      case "level": if (e.source === "tts" || state === "listening") reactor.setLevel(e.value, e.source); break;
      case "wake": reactor.pulse(); setState("listening"); break;
      case "transcript": if (!e.replay) { queue = []; clearTimeout(timer); timer = null; show(`“${e.text}”`, "you"); } break;
      case "speaking": if (!e.replay) say(e.text, e.duration); break;
      case "agenda": agenda = e.events || []; if (state === "idle") idleLine(); break;
      case "announce": if (!e.replay) say(e.text); break;
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
