/* J.A.R.V.I.S. HUD client */
(function () {
  const $ = (id) => document.getElementById(id);
  const reactor = new Reactor($("reactor"));
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  let ws = null, title = "sir", owner = "", booted = false, muted = false;
  let bootLines = 0;

  /* ───────────── clock & salutation ───────────── */
  function partOfDay(h) { return h >= 5 && h < 12 ? "Good morning" : h < 17 && h >= 12 ? "Good afternoon" : "Good evening"; }
  function tickClock() {
    const d = new Date();
    $("clock-time").textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    $("clock-date").textContent = d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    $("salute").textContent = `${partOfDay(d.getHours())}${owner ? ", " + owner : ""}`;
  }
  setInterval(tickClock, 1000); tickClock();

  /* ───────────── boot sequence ───────────── */
  const BOOT_FLAVOUR = [
    "Establishing secure local link", "Calibrating holographic display", "Loading personality matrix",
    "Synchronising chronometer", "Mounting long-term memory", "Spinning up arc reactor",
  ];
  function bootLog(text, cls) {
    const el = document.createElement("div");
    el.textContent = "› " + text;
    if (cls) el.className = cls;
    $("boot-log").appendChild(el);
    while ($("boot-log").children.length > 9) $("boot-log").firstChild.remove();
    bootLines++;
    $("boot-prog").style.strokeDashoffset = String(540 - Math.min(1, bootLines / 12) * 540);
  }
  function finishBoot() {
    if (booted) return;
    booted = true;
    $("boot-prog").style.strokeDashoffset = "0";
    setTimeout(() => { $("boot").classList.add("done"); $("hud").classList.add("on"); }, 600);
  }
  BOOT_FLAVOUR.forEach((line, i) => setTimeout(() => !booted && bootLog(line, "ok"), 180 * i));
  setTimeout(finishBoot, 6000); // never get stuck on the boot screen

  /* ───────────── gauges & sparkline ───────────── */
  function gauge(id, v) {
    const el = $(id); if (!el || v == null) return;
    el.querySelector(".g-val").style.strokeDashoffset = String(264 - (264 * Math.min(100, v)) / 100);
    el.querySelector(".g-num").textContent = Math.round(v);
    el.classList.toggle("hot", v > 85);
  }
  const net = [];
  function fmtRate(b) { return b > 1e6 ? (b / 1e6).toFixed(1) + " MB/s" : (b / 1e3).toFixed(0) + " KB/s"; }
  function drawSpark() {
    const c = $("net-spark"), ctx = c.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    c.width = c.clientWidth * dpr; c.height = 48 * dpr;
    ctx.clearRect(0, 0, c.width, c.height);
    if (net.length < 2) return;
    const max = Math.max(50e3, ...net.map((p) => Math.max(p[0], p[1])));
    [[0, "rgba(25,230,255,0.9)", "rgba(25,230,255,0.12)"], [1, "rgba(255,177,59,0.8)", "rgba(255,177,59,0.06)"]].forEach(([k, stroke, fill]) => {
      ctx.beginPath();
      net.forEach((p, i) => {
        const x = (i / (net.length - 1)) * c.width, y = c.height - (p[k] / max) * (c.height - 4) - 2;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.strokeStyle = stroke; ctx.lineWidth = 1.5 * dpr; ctx.stroke();
      ctx.lineTo(c.width, c.height); ctx.lineTo(0, c.height); ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
    });
  }

  /* ───────────── dialogue (typewriter) ───────────── */
  let replyBuf = "", typed = 0, typingTimer = null, clearTimer = null;
  function showYou(text) {
    $("you").textContent = text;
    replyBuf = ""; typed = 0; $("jarvis").innerHTML = "";
    clearTimeout(clearTimer);
  }
  function addReply(text) {
    replyBuf += (replyBuf ? " " : "") + text;
    if (!typingTimer) typingTimer = setInterval(typeStep, 18);
    clearTimeout(clearTimer);
    clearTimer = setTimeout(() => { $("you").textContent = ""; $("jarvis").textContent = ""; replyBuf = ""; typed = 0; }, 45000);
  }
  function typeStep() {
    if (typed >= replyBuf.length) { clearInterval(typingTimer); typingTimer = null; $("jarvis").textContent = replyBuf; return; }
    typed = Math.min(replyBuf.length, typed + 2);
    const el = $("jarvis");
    el.textContent = replyBuf.slice(0, typed);
    const cur = document.createElement("span"); cur.className = "cursor"; el.appendChild(cur);
    el.scrollTop = el.scrollHeight;
  }

  /* ───────────── activity log ───────────── */
  const NICE = {
    web_search: "WEB SEARCH", look: "OPTICAL SCAN", look_at_screen: "SCREEN CAPTURE", get_calendar: "CALENDAR",
    run_protocol: "PROTOCOL", send_message: "MESSAGE", get_weather: "WEATHER", remember: "MEMORY WRITE", recall: "MEMORY READ",
  };
  const pending = {};
  function logTool(e) {
    const name = NICE[e.name] || e.name.replace(/^mcp__/, "MCP ").replace(/_/g, " ").toUpperCase();
    if (e.status === "planning") return;
    if (e.status === "start") {
      const li = document.createElement("li");
      li.className = "run";
      const args = e.args ? Object.values(e.args).filter((v) => typeof v !== "object").join(" · ").slice(0, 80) : "";
      li.innerHTML = `<span class="k"></span><span class="ms">…</span><span class="sum"></span>`;
      li.querySelector(".k").textContent = "▸ " + name;
      li.querySelector(".sum").textContent = args;
      $("activity").prepend(li);
      pending[e.name] = li;
      while ($("activity").children.length > 40) $("activity").lastChild.remove();
    } else {
      const li = pending[e.name];
      if (!li) return;
      li.className = e.status === "done" ? "ok" : e.status === "declined" ? "err" : e.status === "error" ? "err" : "ok";
      li.querySelector(".ms").textContent = e.ms != null ? e.ms + "ms" : e.status;
      if (e.summary) li.querySelector(".sum").textContent = e.summary;
      delete pending[e.name];
    }
  }
  function logLine(text, cls) {
    const li = document.createElement("li"); li.className = cls || "";
    li.innerHTML = `<span class="k"></span>`; li.querySelector(".k").textContent = text;
    $("activity").prepend(li);
  }

  /* ───────────── panels ───────────── */
  function setWeather(w) {
    $("w-temp").textContent = w.temperature; $("w-unit").textContent = w.unit || "°";
    $("w-cond").textContent = w.condition; $("w-hi").textContent = w.high; $("w-lo").textContent = w.low;
    $("w-rain").textContent = w.rain_chance_12h ?? "--"; $("w-sunset").textContent = w.sunset || "--";
    $("w-wind").textContent = w.wind || "--"; $("w-loc").textContent = (w.location || "").toUpperCase();
  }
  function setAgenda(events) {
    const ul = $("agenda"); ul.innerHTML = "";
    if (!events || !events.length) { ul.innerHTML = '<li class="muted">Clear schedule</li>'; return; }
    const now = Date.now();
    events.slice(0, 8).forEach((e) => {
      const li = document.createElement("li");
      const start = e.start ? new Date(e.start).getTime() : 0, end = e.end ? new Date(e.end).getTime() : 0;
      if (start <= now && now <= end) li.classList.add("now");
      li.innerHTML = `<span class="t"></span><span class="n"></span>`;
      li.querySelector(".t").textContent = e.time || "";
      li.querySelector(".n").textContent = e.title;
      ul.appendChild(li);
    });
  }
  let alarms = [];
  function renderTimers() {
    const ul = $("timers"); ul.innerHTML = "";
    if (!alarms.length) { ul.innerHTML = '<li class="muted">None active</li>'; return; }
    const now = Date.now() / 1000;
    alarms.forEach((a) => {
      const left = Math.max(0, Math.round(a.when - now));
      const li = document.createElement("li");
      const mm = Math.floor(left / 60), ss = String(left % 60).padStart(2, "0");
      li.innerHTML = `<span class="t"></span><span class="n"></span>`;
      li.querySelector(".t").textContent = left > 3600 ? new Date(a.when * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : `${mm}:${ss}`;
      li.querySelector(".n").textContent = a.message;
      ul.appendChild(li);
    });
  }
  setInterval(renderTimers, 1000);
  function setProtocols(items) {
    const box = $("protocols"); box.innerHTML = "";
    (items || []).forEach((p) => {
      const b = document.createElement("button"); b.className = "chip";
      b.textContent = p.name.replace(/_/g, " "); b.title = p.description || "";
      b.onclick = () => send({ type: "protocol", name: p.name });
      box.appendChild(b);
    });
  }

  /* ───────────── optics ───────────── */
  function startCamera() {
    const img = $("cam");
    img.onload = () => { $("p-optics").querySelector(".optics").classList.add("live"); $("optic-id").textContent = "LIVE"; };
    img.onerror = () => { $("optic-id").textContent = "NO FEED"; };
    img.src = "/camera.mjpg?t=" + Date.now();
  }
  function drawFaces(e) {
    const c = $("cam-overlay"), box = c.parentElement;
    const dpr = window.devicePixelRatio || 1;
    c.width = box.clientWidth * dpr; c.height = box.clientHeight * dpr;
    const ctx = c.getContext("2d"); ctx.clearRect(0, 0, c.width, c.height);
    // object-fit: cover mapping
    const scale = Math.max(c.width / e.w, c.height / e.h);
    const ox = (c.width - e.w * scale) / 2, oy = (c.height - e.h * scale) / 2;
    let label = "NO TARGET";
    e.faces.forEach((f) => {
      const [x, y, w, h] = f.box.map((v) => v * scale);
      const X = x + ox, Y = y + oy, k = Math.min(w, h) * 0.25;
      const color = f.name ? "#19e6ff" : "#ff3b5c";
      ctx.strokeStyle = color; ctx.lineWidth = 2 * dpr; ctx.shadowColor = color; ctx.shadowBlur = 8 * dpr;
      ctx.beginPath();
      [[X, Y + k, X, Y, X + k, Y], [X + w - k, Y, X + w, Y, X + w, Y + k], [X, Y + h - k, X, Y + h, X + k, Y + h], [X + w - k, Y + h, X + w, Y + h, X + w, Y + h - k]]
        .forEach(([a, b, c2, d, e2, f2]) => { ctx.moveTo(a, b); ctx.lineTo(c2, d); ctx.lineTo(e2, f2); });
      ctx.stroke();
      ctx.shadowBlur = 0; ctx.fillStyle = color; ctx.font = `${12 * dpr}px "Share Tech Mono", monospace`;
      ctx.fillText((f.name ? f.name.toUpperCase() : "UNIDENTIFIED") + (f.sim ? `  ${Math.round(f.sim * 100)}%` : ""), X, Y - 6 * dpr);
      label = f.name ? "ID: " + f.name.toUpperCase() : "UNKNOWN SUBJECT";
    });
    $("optic-id").textContent = e.faces.length ? label : "LIVE · NO TARGET";
  }

  /* ───────────── state ───────────── */
  const STATE_TEXT = {
    idle: "STANDBY", listening: "LISTENING", transcribing: "PROCESSING", thinking: "THINKING",
    working: "EXECUTING", speaking: "SPEAKING", booting: "INITIALISING", error: "FAULT",
  };
  function setState(s) {
    const shown = muted && s === "idle" ? "muted" : s;
    reactor.setState(shown);
    $("state-label").textContent = muted && s === "idle" ? "MIC MUTED" : (STATE_TEXT[s] || s.toUpperCase());
    $("btn-mic").classList.toggle("active", s === "listening");
  }

  function toast(text, cls) {
    const el = document.createElement("div"); el.className = "toast " + (cls || ""); el.textContent = text;
    $("toasts").appendChild(el); setTimeout(() => el.remove(), 7000);
  }

  /* ───────────── event dispatch ───────────── */
  function handle(e) {
    switch (e.type) {
      case "hello":
        title = e.title || "sir"; owner = e.owner || ""; tickClock();
        (e.history || []).forEach((h) => handle({ ...h, replay: true }));
        setProtocols(e.protocols);
        if (e.camera) startCamera();
        setState(e.state || "idle");
        if (e.state && e.state !== "booting") setTimeout(finishBoot, 900);
        break;
      case "boot":
        if (!booted) bootLog(e.message, e.ok === false ? "bad" : e.ok === null ? "warn" : "ok");
        if (!e.replay) logLine(e.message, e.ok === false ? "err" : "ok");
        break;
      case "capabilities": if (e.camera) startCamera(); break;
      case "state":
        if (e.state !== "booting") finishBoot();
        setState(e.state); break;
      case "level":
        if (e.source === "tts" || reactor.state === "listening") reactor.setLevel(e.value);
        break;
      case "wake": reactor.pulse(); setState("listening"); break;
      case "transcript": if (!e.replay) showYou(e.text); break;
      case "reply": if (!e.replay) addReply(e.text); break;
      case "tool": if (!e.replay) logTool(e); break;
      case "stats":
        gauge("g-cpu", e.cpu); gauge("g-mem", e.mem); gauge("g-disk", e.disk);
        $("net-down").textContent = fmtRate(e.net_down); $("net-up").textContent = fmtRate(e.net_up);
        net.push([e.net_down, e.net_up]); if (net.length > 60) net.shift(); drawSpark();
        if (e.battery != null) $("batt").textContent = (e.charging ? "⚡ " : "▮ ") + e.battery + "%";
        break;
      case "weather": setWeather(e); break;
      case "agenda": setAgenda(e.events); break;
      case "alarms": alarms = e.alarms || []; renderTimers(); break;
      case "protocols": setProtocols(e.items); break;
      case "faces": drawFaces(e); break;
      case "gesture": toast(`Gesture: ${e.gesture.replace(/_/g, " ")} → ${e.action}`); break;
      case "confirm":
        $("confirm-text").textContent = e.action; $("confirm").classList.remove("hidden"); break;
      case "confirm_result": $("confirm").classList.add("hidden"); break;
      case "announce": if (!e.replay) { toast(e.text, e.priority === "high" ? "warn" : ""); } break;
      case "alert": toast(e.message, "err"); break;
      case "error": toast(e.message, "err"); setState("error"); setTimeout(() => setState("idle"), 2500); break;
      case "mic": muted = !!e.muted; $("btn-mute").classList.toggle("on", muted); setState(reactor.state === "muted" ? "idle" : reactor.state); break;
      case "protocol": if (e.status === "start" && !e.replay) toast(`Protocol engaged: ${e.name.replace(/_/g, " ")}`, "warn"); break;
      case "interrupted": $("jarvis").textContent = replyBuf; clearInterval(typingTimer); typingTimer = null; break;
    }
  }

  /* ───────────── socket ───────────── */
  function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); else if (DEMO) demoCommand(msg); }
  function connect() {
    if (DEMO) { runDemo(); return; }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { $("link-status").classList.add("up"); $("link-text").textContent = "LINK ESTABLISHED"; };
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (err) { console.error(err); } };
    ws.onclose = () => {
      $("link-status").classList.remove("up"); $("link-text").textContent = "RECONNECTING…";
      setTimeout(connect, 1500);
    };
  }

  /* ───────────── controls ───────────── */
  $("cmd-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("cmd").value.trim(); if (!text) return;
    send({ type: "text", text }); $("cmd").value = "";
  });
  $("btn-mic").onclick = () => send({ type: "listen" });
  $("btn-stop").onclick = () => send({ type: "stop" });
  $("btn-mute").onclick = () => send({ type: "mute", muted: !muted });
  $("confirm-yes").onclick = () => send({ type: "confirm", approved: true });
  $("confirm-no").onclick = () => send({ type: "confirm", approved: false });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") send({ type: "stop" });
    if (ev.key === " " && document.activeElement !== $("cmd")) { ev.preventDefault(); send({ type: "listen" }); }
    if (ev.key === "/" && document.activeElement !== $("cmd")) { ev.preventDefault(); $("cmd").focus(); }
  });

  /* ───────────── demo mode (open /?demo to preview without a backend) ───────────── */
  function runDemo() {
    $("link-status").classList.add("up"); $("link-text").textContent = "DEMO MODE";
    owner = "Tony"; tickClock();
    const boot = ["J.A.R.V.I.S. core online · 41 abilities registered", "Voice synthesis: kokoro-mlx", "Speech recognition: mlx",
      "Wake word: 'Hey Jarvis'", "Microphone array online", "Optical sensors online", "Facial recognition: tracking Tony",
      "Gesture control online", "Neural link: claude-opus-5-5"];
    boot.forEach((m, i) => setTimeout(() => handle({ type: "boot", message: m, ok: true }), 250 * i));
    setTimeout(() => {
      handle({ type: "state", state: "idle" });
      setWeather({ temperature: 14, unit: "°C", condition: "partly cloudy", high: 17, low: 9, rain_chance_12h: 20, sunset: "16:21", wind: "12 km/h", location: "London, United Kingdom" });
      const t = (h, m) => { const d = new Date(); d.setHours(h, m, 0, 0); return d.toISOString(); };
      setAgenda([{ title: "Stand-up with R&D", time: "9:30 AM – 9:45 AM", start: t(9, 30), end: t(9, 45) },
        { title: "Lunch with Pepper", time: "1:00 PM – 2:00 PM", start: t(13, 0), end: t(14, 0) },
        { title: "Suit diagnostics review", time: "4:00 PM – 5:00 PM", start: t(16, 0), end: t(17, 0) }]);
      alarms = [{ message: "tea", when: Date.now() / 1000 + 240 }]; renderTimers();
      setProtocols([{ name: "work_mode" }, { name: "house_party_protocol" }, { name: "wind_down" }, { name: "good_night" }]);
    }, 2600);
    setInterval(() => handle({ type: "stats", cpu: 18 + Math.random() * 25, mem: 61 + Math.random() * 4, disk: 47,
      net_down: Math.random() * 900e3, net_up: Math.random() * 200e3, battery: 82, charging: true }), 2000);
    const script = [
      [3200, { type: "state", state: "thinking" }],
      [4400, { type: "state", state: "speaking" }],
      [4500, { type: "reply", text: "Good morning, Tony. It's fourteen degrees and partly cloudy in London." }],
      [7600, { type: "reply", text: "You have three things today, starting with stand-up at half past nine." }],
      [10800, { type: "reply", text: "What shall we tackle first?" }],
      [12500, { type: "state", state: "listening" }],
      [14500, { type: "transcript", text: "Engage work mode and tell me what's on my screen." }],
      [15000, { type: "state", state: "working" }],
      [15100, { type: "tool", name: "run_protocol", status: "start", args: { name: "work mode" } }],
      [16300, { type: "tool", name: "run_protocol", status: "done", ms: 1180, summary: "Protocol work_mode complete." }],
      [16400, { type: "tool", name: "look_at_screen", status: "start", args: {} }],
      [17200, { type: "tool", name: "look_at_screen", status: "done", ms: 640, summary: "[+1 image]" }],
      [17800, { type: "state", state: "speaking" }],
      [17900, { type: "reply", text: "Work mode engaged. You've got Xcode open with a failing build: a missing import in SuitTelemetry.swift." }],
      [22500, { type: "state", state: "idle" }],
    ];
    const play = () => script.forEach(([ms, ev]) => setTimeout(() => handle(ev), ms));
    play(); setInterval(play, 26000);
    // fake audio levels while speaking/listening
    setInterval(() => { if (["speaking", "listening"].includes(reactor.state)) reactor.setLevel(0.25 + Math.random() * 0.6); }, 60);
  }
  function demoCommand(msg) {
    if (msg.type === "text") { handle({ type: "transcript", text: msg.text }); setTimeout(() => handle({ type: "reply", text: "Demo mode, sir. Connect me to the real backend and I'll do that for you." }), 700); }
  }

  connect();
})();
