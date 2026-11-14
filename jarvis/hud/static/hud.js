/* J.A.R.V.I.S. HUD client — talks to the Python core over a WebSocket. */
(function () {
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const reactor = new Reactor($("core-gl"), $("core-ui"));
  const DEMO = new URLSearchParams(location.search).has("demo");
  let ws = null, owner = "", booted = false, muted = false, state = "booting";

  /* ───────────── time ───────────── */
  function partOfDay(h) { return h >= 5 && h < 12 ? "Good morning" : h >= 12 && h < 17 ? "Good afternoon" : "Good evening"; }
  function tick() {
    const d = new Date();
    const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(d);
    const hm = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    const period = (parts.find((p) => p.type === "dayPeriod") || {}).value || "";
    $("time").innerHTML = `${hm}<span class="sec">${String(d.getSeconds()).padStart(2, "0")}</span>` +
      (period ? `<span class="ampm">${period}</span>` : "");
    $("date").textContent = d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
    $("salute").textContent = `${partOfDay(d.getHours())}${owner ? ", " + owner : ""}.`;
  }
  setInterval(tick, 1000); tick();

  /* ───────────── state ───────────── */
  const STATUS = {
    booting: "Initialising", idle: "Standby", listening: "Listening", transcribing: "Processing",
    thinking: "Thinking", working: "Executing", speaking: "Speaking", error: "Fault", muted: "Microphone muted",
  };
  function setState(s) {
    state = s;
    const shown = muted && (s === "idle") ? "muted" : s;
    body.dataset.state = shown;
    reactor.setState(shown);
    $("status-text").textContent = STATUS[shown] || s;
  }
  function finishBoot() {
    if (booted) return;
    booted = true;
    body.classList.remove("booting");
    if (state === "booting") setState("idle");
  }
  setTimeout(finishBoot, 5000);

  /* ───────────── dialogue ───────────── */
  let clearTimer = null;
  function showYou(text) {
    $("you").textContent = text;
    $("jarvis").innerHTML = "";
    clearTimeout(clearTimer);
  }
  function addReply(text) {
    const el = $("jarvis");
    const words = text.split(/(\s+)/);
    let i = 0;
    if (el.childNodes.length) el.appendChild(document.createTextNode(" "));
    for (const w of words) {
      if (!w) continue;
      if (/^\s+$/.test(w)) { el.appendChild(document.createTextNode(" ")); continue; }
      const span = document.createElement("span");
      span.className = "w"; span.textContent = w;
      span.style.animationDelay = `${i++ * 38}ms`;
      el.appendChild(span);
    }
    // keep only what fits: drop the oldest words as new ones arrive
    const max = parseFloat(getComputedStyle(el).maxHeight) || 9999;
    while (el.scrollHeight > max + 6 && el.firstChild) el.removeChild(el.firstChild);
    clearTimeout(clearTimer);
    clearTimer = setTimeout(() => { $("you").textContent = ""; el.innerHTML = ""; }, 60000);
  }

  /* ───────────── weather ───────────── */
  const ICONS = {
    sun: '<circle cx="24" cy="24" r="8"/><path d="M24 6v4M24 38v4M6 24h4M38 24h4M11.3 11.3l2.8 2.8M33.9 33.9l2.8 2.8M11.3 36.7l2.8-2.8M33.9 14.1l2.8-2.8"/>',
    moon: '<path d="M31 9a15 15 0 1 0 8 23 12 12 0 0 1-8-23Z"/>',
    partly: '<path d="M17 12v2M8.5 15.5l1.4 1.4M7 24h2M25.5 15.5l-1.4 1.4"/><path d="M12 22a6 6 0 0 1 11-3"/><path d="M16 37h17a7 7 0 1 0-2-13.7A9 9 0 0 0 14 27a5 5 0 0 0 2 10Z"/>',
    cloud: '<path d="M14 36h20a8 8 0 1 0-2.3-15.7A10 10 0 0 0 12 24a6 6 0 0 0 2 12Z"/>',
    fog: '<path d="M10 20h28M6 27h36M12 34h24"/>',
    rain: '<path d="M14 28h20a8 8 0 1 0-2.3-15.7A10 10 0 0 0 12 16a6 6 0 0 0 2 12Z"/><path d="M17 33l-2 5M25 33l-2 5M33 33l-2 5"/>',
    snow: '<path d="M14 28h20a8 8 0 1 0-2.3-15.7A10 10 0 0 0 12 16a6 6 0 0 0 2 12Z"/><path d="M17 35v.1M24 38v.1M31 35v.1"/>',
    storm: '<path d="M14 28h20a8 8 0 1 0-2.3-15.7A10 10 0 0 0 12 16a6 6 0 0 0 2 12Z"/><path d="M25 30l-4 7h6l-4 7"/>',
  };
  function iconFor(code, day) {
    if (code === 0 || code === 1) return day === false ? "moon" : "sun";
    if (code === 2) return "partly";
    if (code === 3) return "cloud";
    if (code === 45 || code === 48) return "fog";
    if (code >= 71 && code <= 77 || code === 85 || code === 86) return "snow";
    if (code >= 95) return "storm";
    if (code >= 51) return "rain";
    return "cloud";
  }
  function setWeather(w) {
    $("w-temp").textContent = w.temperature;
    $("w-cond").textContent = w.condition;
    $("w-hi").textContent = w.high; $("w-lo").textContent = w.low;
    $("w-rain").textContent = w.rain_chance_12h ?? "--";
    $("w-loc").textContent = (w.location || "").split(",")[0];
    $("wx-icon").innerHTML = ICONS[iconFor(w.code, w.is_day)] || ICONS.cloud;
  }

  /* ───────────── agenda ───────────── */
  let agenda = [];
  function rel(ms) {
    const m = Math.round(ms / 60000);
    if (m < 60) return `in ${m} min`;
    const h = Math.floor(m / 60), mm = m % 60;
    return `in ${h}h${mm ? " " + mm + "m" : ""}`;
  }
  function renderAgenda() {
    const ol = $("agenda"); ol.innerHTML = "";
    const timed = agenda.filter((e) => !e.all_day);
    if (!agenda.length) { ol.innerHTML = '<li class="empty">Clear schedule</li>'; return; }
    const now = Date.now();
    let nextMarked = false;
    agenda.slice(0, 7).forEach((e, i) => {
      const li = document.createElement("li");
      li.style.animationDelay = `${i * 60}ms`;
      const s = e.start ? new Date(e.start).getTime() : 0, f = e.end ? new Date(e.end).getTime() : s;
      let relText = "";
      if (!e.all_day && s <= now && now <= f) li.classList.add("now");
      else if (!e.all_day && f < now) li.classList.add("past");
      else if (!e.all_day && !nextMarked && s > now) { li.classList.add("next"); nextMarked = true; relText = rel(s - now); }
      const t = document.createElement("span"); t.className = "t";
      t.textContent = e.all_day ? "All day" : (e.time || "").replace(/\s?–\s?.*/, "");
      if (li.classList.contains("now")) t.textContent += "  ·  now";
      if (relText) { const r = document.createElement("span"); r.className = "rel"; r.textContent = relText; t.appendChild(r); }
      const n = document.createElement("span"); n.className = "n"; n.textContent = e.title;
      li.append(t, n); ol.appendChild(li);
    });
    reactor.setEvents(timed);
  }
  setInterval(() => agenda.length && renderAgenda(), 60000);

  /* ───────────── timers ───────────── */
  let alarms = [];
  function renderTimers() {
    const ul = $("timers"); ul.innerHTML = "";
    $("timers-h").hidden = !alarms.length;
    const now = Date.now() / 1000;
    alarms.forEach((a) => {
      const left = Math.max(0, Math.round(a.when - now));
      const li = document.createElement("li");
      const c = document.createElement("span"); c.className = "c";
      c.textContent = left >= 3600
        ? new Date(a.when * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
        : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      const n = document.createElement("span"); n.textContent = a.message;
      li.append(n, c); ul.appendChild(li);
    });
  }
  setInterval(renderTimers, 1000);

  /* ───────────── activity ───────────── */
  const FRIENDLY = {
    web_search: "Searching the web", get_calendar: "Reading your calendar", create_calendar_event: "Scheduling",
    get_reminders: "Checking reminders", create_reminder: "Setting a reminder", look: "Looking through the camera",
    look_at_screen: "Reading your screen", play_music: "Cueing music", media_control: "Media control",
    send_message: "Sending a message", get_weather: "Checking the weather", get_news: "Pulling headlines",
    run_protocol: "Running protocol", run_shell: "Running a command", run_applescript: "Running AppleScript",
    run_shortcut: "Running a Shortcut", open_application: "Opening app", quit_application: "Closing app",
    open_url: "Opening link", system_status: "Running diagnostics", remember: "Committing to memory",
    recall: "Searching memory", set_timer: "Starting a timer", set_alarm: "Setting an alarm",
    set_volume: "Adjusting volume", set_brightness: "Adjusting brightness", find_files: "Searching files",
    read_text_file: "Reading a file", who_is_here: "Identifying", learn_face: "Learning a face",
    create_note: "Writing a note", system_action: "System action", show_notification: "Notifying",
  };
  function friendly(name) {
    if (FRIENDLY[name]) return FRIENDLY[name];
    const n = name.replace(/^mcp__([^_]+)__/, "$1 · ").replace(/_/g, " ");
    return n.charAt(0).toUpperCase() + n.slice(1);
  }
  const running = {};
  function argText(args) {
    if (!args) return "";
    return Object.values(args).filter((v) => typeof v !== "object").join(" · ").slice(0, 90);
  }
  function activityRow(cls, title, sub, ms) {
    const li = document.createElement("li"); li.className = cls;
    li.innerHTML = '<span class="d"></span><span class="a"></span><span class="ms"></span><span class="s"></span>';
    li.querySelector(".a").textContent = title;
    li.querySelector(".s").textContent = sub || "";
    li.querySelector(".ms").textContent = ms || "";
    if (!sub) li.querySelector(".s").remove();
    const ol = $("activity"); ol.prepend(li);
    while (ol.children.length > 24) ol.lastChild.remove();
    return li;
  }
  function logTool(e) {
    if (e.status === "planning") return;
    if (e.status === "start") { running[e.name] = activityRow("run", friendly(e.name), argText(e.args), "…"); return; }
    const li = running[e.name]; delete running[e.name];
    if (!li) return;
    li.className = e.status === "done" ? "" : "err";
    li.querySelector(".ms").textContent = e.ms != null ? (e.ms >= 1000 ? (e.ms / 1000).toFixed(1) + "s" : e.ms + "ms") : e.status;
    if (e.summary) {
      let s = li.querySelector(".s");
      if (!s) { s = document.createElement("span"); s.className = "s"; li.appendChild(s); }
      s.textContent = e.summary;
    }
  }

  /* ───────────── vitals ───────────── */
  const net = [];
  function fmtRate(b) { return b > 1e6 ? (b / 1e6).toFixed(1) + "M" : (b / 1e3).toFixed(0) + "K"; }
  function bar(id, nid, v) {
    if (v == null) return;
    const el = $(id); el.style.width = Math.min(100, v) + "%"; el.classList.toggle("hot", v > 85);
    $(nid).textContent = Math.round(v) + "%";
  }
  function spark() {
    const c = $("net-spark"), dpr = window.devicePixelRatio || 1;
    c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr;
    const ctx = c.getContext("2d"); ctx.clearRect(0, 0, c.width, c.height);
    if (net.length < 2) return;
    const max = Math.max(60e3, ...net);
    const g = ctx.createLinearGradient(0, 0, c.width, 0);
    g.addColorStop(0, "rgba(143,236,255,0.05)"); g.addColorStop(1, "rgba(143,236,255,0.9)");
    ctx.beginPath();
    net.forEach((v, i) => {
      const x = (i / (net.length - 1)) * c.width, y = c.height - (v / max) * (c.height - 2) - 1;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = g; ctx.lineWidth = 1.2 * dpr; ctx.stroke();
  }

  /* ───────────── protocols ───────────── */
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
    if (img.dataset.on) return;
    img.dataset.on = "1";
    img.onload = () => { $("optics").classList.add("live"); if ($("optic-id").textContent === "No feed") $("optic-id").textContent = "Live"; };
    img.onerror = () => { $("optics").classList.remove("live"); $("optic-id").textContent = "No feed"; delete img.dataset.on; };
    img.src = "/camera.mjpg?t=" + Date.now();
  }
  function drawFaces(e) {
    const c = $("cam-overlay"), box = c.parentElement, dpr = window.devicePixelRatio || 1;
    c.width = box.clientWidth * dpr; c.height = box.clientHeight * dpr;
    const ctx = c.getContext("2d"); ctx.clearRect(0, 0, c.width, c.height);
    const scale = Math.max(c.width / e.w, c.height / e.h);
    const ox = (c.width - e.w * scale) / 2, oy = (c.height - e.h * scale) / 2;
    let tag = "Live · no one in view";
    e.faces.forEach((f) => {
      const [x, y, w, h] = f.box.map((v) => v * scale);
      const X = x + ox, Y = y + oy, k = Math.min(w, h) * 0.22, rad = 6 * dpr;
      const col = f.name ? "143,236,255" : "255,107,107";
      ctx.strokeStyle = `rgba(${col},0.95)`; ctx.lineWidth = 1.6 * dpr; ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(X, Y + k); ctx.arcTo(X, Y, X + k, Y, rad); ctx.lineTo(X + k, Y);
      ctx.moveTo(X + w - k, Y); ctx.arcTo(X + w, Y, X + w, Y + k, rad); ctx.lineTo(X + w, Y + k);
      ctx.moveTo(X, Y + h - k); ctx.arcTo(X, Y + h, X + k, Y + h, rad); ctx.lineTo(X + k, Y + h);
      ctx.moveTo(X + w - k, Y + h); ctx.arcTo(X + w, Y + h, X + w, Y + h - k, rad); ctx.lineTo(X + w, Y + h - k);
      ctx.stroke();
      ctx.fillStyle = `rgba(${col},0.06)`; ctx.fillRect(X, Y, w, h);
      tag = f.name ? `${f.name} · ${Math.round((f.sim || 0) * 100)}% match` : "Unidentified";
    });
    $("optic-id").textContent = tag;
  }

  /* ───────────── island notifications ───────────── */
  function island(text, cls) {
    const el = document.createElement("div"); el.className = "isle " + (cls || "");
    el.innerHTML = '<span class="d"></span><span></span>'; el.lastChild.textContent = text;
    $("island").appendChild(el);
    while ($("island").children.length > 3) $("island").firstChild.remove();
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 400); }, 5200);
  }

  /* ───────────── event dispatch ───────────── */
  function handle(e) {
    switch (e.type) {
      case "hello":
        owner = e.owner || ""; tick();
        (e.history || []).forEach((h) => handle({ ...h, replay: true }));
        setProtocols(e.protocols);
        if (e.camera) startCamera();
        setState(e.state || "idle");
        if (e.state && e.state !== "booting") setTimeout(finishBoot, 1400);
        break;
      case "boot":
        if (!booted) $("bootline").textContent = e.message;
        if (!e.replay) activityRow("sys", e.message);
        break;
      case "capabilities": if (e.camera) startCamera(); break;
      case "state": setState(e.state); if (e.state !== "booting") setTimeout(finishBoot, 1200); break;
      case "level":
        if (e.source === "tts" || state === "listening") reactor.setLevel(e.value, e.source);
        break;
      case "wake": reactor.pulse(); setState("listening"); break;
      case "transcript": if (!e.replay) showYou(e.text); break;
      case "reply": if (!e.replay) addReply(e.text); break;
      case "tool": if (!e.replay) logTool(e); break;
      case "stats":
        bar("v-cpu", "n-cpu", e.cpu); bar("v-mem", "n-mem", e.mem); bar("v-disk", "n-disk", e.disk);
        net.push((e.net_down || 0) + (e.net_up || 0)); if (net.length > 40) net.shift(); spark();
        $("n-net").textContent = fmtRate(e.net_down || 0);
        if (e.battery != null) $("batt").textContent = `${e.battery}%${e.charging ? " · charging" : ""}`;
        break;
      case "weather": setWeather(e); break;
      case "agenda": agenda = e.events || []; renderAgenda(); break;
      case "alarms": alarms = e.alarms || []; renderTimers(); break;
      case "protocols": setProtocols(e.items); break;
      case "faces": drawFaces(e); break;
      case "gesture": island(`${e.gesture.replace(/_/g, " ")} → ${e.action.replace(/_/g, " ")}`); break;
      case "confirm": $("confirm-text").textContent = e.action; $("confirm").hidden = false; break;
      case "confirm_result": $("confirm").hidden = true; break;
      case "announce": if (!e.replay) island(e.text, e.priority === "high" ? "warn" : ""); break;
      case "alert": island(e.message, "err"); break;
      case "error": island(e.message, "err"); setState("error"); setTimeout(() => setState("idle"), 2600); break;
      case "mic": muted = !!e.muted; $("btn-mute").classList.toggle("on", muted); setState(state); break;
      case "protocol": if (e.status === "start" && !e.replay) island(`Protocol engaged · ${e.name.replace(/_/g, " ")}`, "warn"); break;
    }
  }

  /* ───────────── socket ───────────── */
  function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); else if (DEMO) demoCommand(msg); }
  function connect() {
    if (DEMO) { runDemo(); return; }
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { $("link").classList.add("up"); $("link-text").textContent = "Linked"; };
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (err) { console.error(err); } };
    ws.onclose = () => { $("link").classList.remove("up"); $("link-text").textContent = "Reconnecting"; setTimeout(connect, 1500); };
  }

  /* ───────────── controls ───────────── */
  $("cmd-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("cmd").value.trim(); if (!text) return;
    send({ type: "text", text }); $("cmd").value = ""; $("cmd").blur();
  });
  $("btn-mic").onclick = () => send({ type: "listen" });
  $("btn-stop").onclick = () => send({ type: "stop" });
  $("btn-mute").onclick = () => send({ type: "mute", muted: !muted });
  $("confirm-yes").onclick = () => send({ type: "confirm", approved: true });
  $("confirm-no").onclick = () => send({ type: "confirm", approved: false });
  document.addEventListener("keydown", (ev) => {
    const typing = document.activeElement === $("cmd");
    if (ev.key === "Escape") { send({ type: "stop" }); $("cmd").blur(); }
    if (ev.key === " " && !typing) { ev.preventDefault(); send({ type: "listen" }); }
    if (ev.key === "/" && !typing) { ev.preventDefault(); $("cmd").focus(); }
  });

  /* ───────────── demo mode: /?demo previews the HUD without a backend ───────────── */
  function runDemo() {
    $("link").classList.add("up"); $("link-text").textContent = "Demo";
    owner = "Tony"; tick();
    ["Core online · 44 abilities", "Voice synthesis · kokoro-mlx", "Speech recognition · mlx", "Wake word · Hey Jarvis",
      "Optical sensors online", "Facial recognition · tracking Tony", "Neural link · claude-opus-5-5"]
      .forEach((m, i) => setTimeout(() => handle({ type: "boot", message: m, ok: true }), 260 * i));
    const at = (mins) => new Date(Date.now() + mins * 60e3);
    const span = (a, b) => `${at(a).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} – ${at(b).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
    setTimeout(() => {
      handle({ type: "state", state: "idle" });
      setWeather({ temperature: 14, condition: "partly cloudy", code: 2, is_day: true, high: 17, low: 9, rain_chance_12h: 20, location: "London, United Kingdom" });
      handle({ type: "agenda", events: [
        { title: "Stand-up with R&D", time: span(-150, -135), start: at(-150).toISOString(), end: at(-135).toISOString() },
        { title: "Suit telemetry review", time: span(-20, 25), start: at(-20).toISOString(), end: at(25).toISOString() },
        { title: "Lunch with Pepper", time: span(55, 115), start: at(55).toISOString(), end: at(115).toISOString() },
        { title: "Board call — Stark Industries", time: span(210, 270), start: at(210).toISOString(), end: at(270).toISOString() },
      ] });
      handle({ type: "alarms", alarms: [{ message: "Tea", when: Date.now() / 1000 + 244 }] });
      setProtocols([{ name: "work_mode" }, { name: "house_party_protocol" }, { name: "wind_down" }, { name: "good_night" }]);
    }, 2300);
    setInterval(() => handle({ type: "stats", cpu: 14 + Math.random() * 22, mem: 58 + Math.random() * 5, disk: 47,
      net_down: Math.random() * 900e3, net_up: Math.random() * 120e3, battery: 82, charging: true }), 1500);
    const script = [
      [3000, { type: "state", state: "thinking" }],
      [4200, { type: "state", state: "speaking" }],
      [4300, { type: "reply", text: "Good afternoon, Tony. Fourteen degrees and partly cloudy in London." }],
      [7600, { type: "reply", text: "Your telemetry review runs another twenty five minutes, then lunch with Pepper." }],
      [11200, { type: "reply", text: "What shall we tackle next?" }],
      [13200, { type: "state", state: "listening" }],
      [15200, { type: "transcript", text: "Engage work mode, and tell me what's on my screen." }],
      [15700, { type: "state", state: "working" }],
      [15800, { type: "tool", name: "run_protocol", status: "start", args: { name: "work mode" } }],
      [16900, { type: "tool", name: "run_protocol", status: "done", ms: 1080, summary: "Volume 25% · Calendar opened · Deep Focus playing" }],
      [17000, { type: "tool", name: "look_at_screen", status: "start", args: {} }],
      [17700, { type: "tool", name: "look_at_screen", status: "done", ms: 640, summary: "Screenshot of the user's screen" }],
      [18200, { type: "state", state: "speaking" }],
      [18300, { type: "reply", text: "Work mode engaged. Xcode is showing a failed build: SuitTelemetry.swift is missing an import of Combine." }],
      [24500, { type: "state", state: "idle" }],
    ];
    const play = () => script.forEach(([ms, ev]) => setTimeout(() => handle(ev), ms));
    play(); setInterval(play, 28000);
    setInterval(() => { if (["speaking", "listening"].includes(state)) reactor.setLevel(0.2 + Math.random() * 0.6, state === "listening" ? "mic" : "tts"); }, 70);
  }
  function demoCommand(msg) {
    if (msg.type === "text") {
      handle({ type: "transcript", text: msg.text });
      setTimeout(() => { handle({ type: "state", state: "speaking" }); handle({ type: "reply", text: "This is a preview, sir. Start me properly and I'll take care of it." }); }, 800);
      setTimeout(() => handle({ type: "state", state: "idle" }), 4000);
    }
    if (msg.type === "listen") handle({ type: "state", state: "listening" });
    if (msg.type === "stop") handle({ type: "state", state: "idle" });
  }

  connect();
})();
