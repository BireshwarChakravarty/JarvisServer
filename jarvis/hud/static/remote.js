/* JARVIS companion (iPhone / iPad). Pairs once from the Mac's QR code, then keeps a live link.
 *
 * Reliability: iOS freezes pages in the background and can leave a dead connection that still
 * looks open. So the page pings the Mac, reconnects the moment it comes back to the screen, queues
 * taps made while reconnecting, and every command is acknowledged by the Mac: you always see
 * whether it worked.
 *
 * Voice (needs the secure connection, see the setup sheet): the microphone is streamed to the Mac
 * as 16 kHz audio; the Mac listens, transcribes, answers, and sends his voice back to play here. */
(() => {
  const $ = (id) => document.getElementById(id);
  const DEMO = new URLSearchParams(location.search).has("demo");
  const SECURE = location.protocol === "https:";
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
                  del: (k) => { try { localStorage.removeItem(k); } catch (e) {} } };
  const LABEL = { idle: "Standby", listening: "Listening", transcribing: "Hearing you", thinking: "Thinking",
    working: "On it", speaking: "Speaking", error: "Fault", muted: "Mac mic muted", booting: "Starting" };
  let reactor = null, ws = null, backoff = 800, muted = false, hands = false, macState = "idle";
  let lastMsg = 0, pingTimer = null, reconnectTimer = null, securePort = 7779;

  function deviceName() {
    const ua = navigator.userAgent;
    if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
    if (/iPhone/.test(ua)) return "iPhone";
    return "Browser";
  }
  function toast(text, ms = 2600) {
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), ms);
  }

  // ── pairing ──────────────────────────────────────────────────────────
  async function start() {
    const h = new URLSearchParams(location.hash.slice(1));
    const code = h.get("pair"), handoff = h.get("token");
    if ((code || handoff) && !DEMO) history.replaceState(null, "", location.pathname);   // never left in the address bar
    if (handoff) store.set("jarvis-token", handoff);          // arriving from the plain page, now secure
    if (code && !DEMO) {
      try {
        const r = await fetch("/remote/api/pair", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, name: deviceName() }) });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || "Pairing failed");
        store.set("jarvis-token", data.token);
      } catch (e) { $("pair-err").textContent = e.message; }
    }
    if (!store.get("jarvis-token") && !DEMO) { $("unpaired").hidden = false; return; }
    $("app").hidden = false;
    reactor = new Reactor($("core-gl"), $("core-ui"), { compact: true });
    reactor.setState("idle");
    loadPrefs();
    if (DEMO) return demo();
    connect();
    if (h.get("voice") === "1") toast("Secure connection ready. Tap the microphone to talk.", 4000);
    if (!SECURE) checkSecure(true);
  }

  // ── link ─────────────────────────────────────────────────────────────
  function connect() {
    clearTimeout(reconnectTimer);
    if (ws) { ws.onclose = ws.onmessage = ws.onopen = null; try { ws.close(); } catch (e) {} }
    const proto = SECURE ? "wss" : "ws";
    $("link").textContent = "Connecting…";
    const sock = new WebSocket(`${proto}://${location.host}/remote/ws?token=${encodeURIComponent(store.get("jarvis-token") || "")}`);
    sock.binaryType = "arraybuffer";
    ws = sock;
    sock.onopen = () => {
      backoff = 800; lastMsg = Date.now();
      $("led").classList.add("up"); $("link").textContent = "Connected";
      flushQueue();
      clearInterval(pingTimer);
      pingTimer = setInterval(heartbeat, 8000);
      if (voice.mode !== "off") sendVoiceMode(voice.mode);       // resume voice after a reconnect
    };
    sock.onmessage = (m) => {
      lastMsg = Date.now();
      if (typeof m.data !== "string") { audioIn(m.data); return; }
      try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); }
    };
    sock.onclose = (e) => {
      if (sock !== ws) return;
      $("led").classList.remove("up"); clearInterval(pingTimer);
      if (e.code === 4401) {          // this device was forgotten on the Mac
        store.del("jarvis-token"); $("app").hidden = true; $("unpaired").hidden = false;
        $("pair-err").textContent = "This device was unpaired. Scan a new code to pair again."; return;
      }
      $("link").textContent = "Reconnecting…";
      reconnectTimer = setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 10000);
    };
  }
  const open = () => ws && ws.readyState === 1;
  function heartbeat() {
    if (!open()) return;
    if (Date.now() - lastMsg > 20000) { connect(); return; }      // silent for too long: it's dead
    ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
  }
  // Back on screen (or network back): prove the link is alive within 2.5 s, else start a fresh one.
  function wake() {
    if (DEMO || $("app").hidden) return;
    if (!open()) { connect(); return; }
    const before = lastMsg;
    ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
    setTimeout(() => { if (lastMsg === before) connect(); }, 2500);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { wake(); voiceResume(); } });
  window.addEventListener("pageshow", (e) => { if (e.persisted) wake(); });
  window.addEventListener("online", wake);

  // ── commands: queued while reconnecting, acknowledged by the Mac ──────
  let nextId = 1;
  const pending = new Map(), queue = [];
  function send(msg, btn) {
    if (DEMO) { if (btn) flash(btn, true); return; }
    const id = nextId++;
    const entry = { msg: { ...msg, id }, btn, at: Date.now() };
    if (btn) btn.classList.add("busy");
    if (open()) transmit(entry);
    else { queue.push(entry); toast("Reconnecting to your Mac…"); wake(); }
  }
  function transmit(entry) {
    pending.set(entry.msg.id, entry);
    ws.send(JSON.stringify(entry.msg));
    entry.timer = setTimeout(() => {
      if (!pending.has(entry.msg.id)) return;
      pending.delete(entry.msg.id);
      if (entry.btn) flash(entry.btn, false);
      toast("Your Mac didn't answer. Reconnecting…");
      connect();
    }, 6000);
  }
  function flushQueue() {
    while (queue.length) {
      const e = queue.shift();
      if (Date.now() - e.at < 20000) transmit(e);
      else if (e.btn) e.btn.classList.remove("busy");
    }
  }
  function ack(a) {
    const e = pending.get(a.id); if (!e) return;
    clearTimeout(e.timer); pending.delete(a.id);
    if (e.btn) flash(e.btn, a.ok);
    if (a.note && (!a.ok || e.btn)) toast(a.note);            // successes only for the buttons you tapped
  }
  function flash(btn, ok) {
    btn.classList.remove("busy", "ok", "fail"); void btn.offsetWidth;
    btn.classList.add(ok ? "ok" : "fail");
    setTimeout(() => btn.classList.remove("ok", "fail"), 800);
  }

  // ── subtitles: what he's saying, two lines at a time, paced to his voice ──
  const measurer = document.createElement("canvas").getContext("2d");
  let subTimers = [];
  function lines(text) {
    const el = $("jarvis"), cs = getComputedStyle(el);
    measurer.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const max = el.clientWidth * 0.94, out = []; let line = "";
    for (const w of text.split(/\s+/).filter(Boolean)) {
      const t = line ? line + " " + w : w;
      if (measurer.measureText(t).width <= max || !line) line = t; else { out.push(line); line = w; }
    }
    if (line) out.push(line);
    return out;
  }
  // two lines per block, ending a block at a comma or full stop when one falls in its latter part
  function blocksOf(text) {
    const blocks = [];
    let rest = text.trim();
    while (rest) {
      const ls = lines(rest);
      let block = ls.slice(0, 2).join(" ");
      if (ls.length > 2) {
        const cut = Math.max(block.lastIndexOf(", "), block.lastIndexOf(". "), block.lastIndexOf("; "),
          block.lastIndexOf("? "), block.lastIndexOf("! "));
        if (cut > block.length * 0.35) block = block.slice(0, cut + 1);
      }
      blocks.push(block);
      rest = rest.slice(block.length).trim();
    }
    return blocks;
  }
  function showBlock(t) {
    const el = $("jarvis"); el.innerHTML = "";
    t.split(" ").forEach((w, i) => { const s = document.createElement("span"); s.className = "w"; s.textContent = w + " ";
      s.style.animationDelay = `${i * 28}ms`; el.appendChild(s); });
  }
  function subtitle(text, duration) {
    subTimers.forEach(clearTimeout); subTimers = [];
    const bs = blocksOf(text), total = bs.reduce((n, b) => n + b.length, 0) || 1;
    const dur = (duration || text.length / 15) * 1000;
    let at = 0;
    bs.forEach((b) => { subTimers.push(setTimeout(() => showBlock(b), at)); at += dur * (b.length / total); });
  }
  let lastSpoke = 0;

  // ── events ───────────────────────────────────────────────────────────
  function setState(s) {
    macState = s;
    document.body.dataset.state = s; reactor && reactor.setState(s);
    let label = muted && s === "idle" ? LABEL.muted : LABEL[s] || s;
    if (s === "idle" && voice.mode === "wake") label = "Say “Hey JARVIS”";
    $("state").textContent = label;
  }
  function handle(e) {
    switch (e.type) {
      case "hello":
        setState(e.state || "idle");
        if (e.voice && e.voice.secure_port) securePort = e.voice.secure_port;
        break;
      case "pong": break;
      case "ack": ack(e); break;
      case "state": setState(e.state); break;
      case "level": if (reactor && (e.source === "tts" || macState === "listening") && !voice.capturing) reactor.setLevel(e.value, e.source); break;
      case "transcript": if (!e.replay) { $("you").textContent = e.text; $("jarvis").textContent = ""; } break;
      case "speaking": if (!e.replay) { lastSpoke = Date.now(); subtitle(e.text, e.duration); } break;
      case "reply": if (!e.replay && Date.now() - lastSpoke > 30000) subtitle(e.text); break;
      case "confirm": $("confirm-q").textContent = e.action; $("confirm").hidden = false; break;
      case "confirm_result": $("confirm").hidden = true; break;
      case "mic": muted = !!e.muted; $("c-mute").setAttribute("aria-pressed", muted); setState(macState); break;
      case "hand_control": hands = !!e.on; $("c-hand").setAttribute("aria-pressed", hands); break;
      case "announce": if (!e.replay) toast(e.text, 3500); break;
      case "error": if (!e.replay) toast(e.message, 3500); break;
      case "interrupted": stopPlayback(); break;
      case "weather": if (e.temperature != null) $("wx").textContent = `${Math.round(e.temperature)}° ${e.condition || ""}`; break;
      case "agenda": nextUp(e.events || []); break;
      case "protocols": protocols(e.items || []); break;
      // voice
      case "audio": audioMeta = e; break;
      case "voice": voicePhase(e); break;
      case "wake": chime([880, 1320]); break;
      case "heard": if (e.text) $("you").textContent = e.text; break;
      case "dismissed": chime([1320, 990]); break;
    }
  }
  function nextUp(events) {
    const now = Date.now();
    const e = events.filter((x) => x.start && !x.all_day && new Date(x.end || x.start).getTime() > now)
      .sort((a, b) => new Date(a.start) - new Date(b.start))[0];
    const box = $("next");
    if (!e) { box.hidden = true; return; }
    const s = new Date(e.start).getTime();
    box.innerHTML = "<span></span> <b></b>";
    box.querySelector("span").textContent = s <= now ? "Now ·" : new Date(s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) + " ·";
    box.querySelector("b").textContent = e.title;
    box.hidden = false;
  }
  function protocols(items) {
    const box = $("protocols"); box.innerHTML = "";
    items.forEach((p) => {
      const b = document.createElement("button"); b.className = "chip"; b.textContent = p.name.replace(/_/g, " ");
      b.onclick = () => send({ type: "protocol", name: p.name }, b);
      box.appendChild(b);
    });
  }

  // ── controls ─────────────────────────────────────────────────────────
  const input = $("text");
  input.addEventListener("input", () => { const has = !!input.value.trim(); $("send").hidden = !has; $("mic").hidden = has; });
  $("ask").onsubmit = (ev) => {
    ev.preventDefault();
    const t = input.value.trim(); if (!t) return;
    send({ type: "text", text: t, speak: prefs.speak && SECURE });
    unlockAudio();
    input.value = ""; input.blur(); $("send").hidden = true; $("mic").hidden = false;
    $("you").textContent = t;
  };
  $("c-stop").onclick = (e) => { stopPlayback(); send({ type: "stop" }, e.currentTarget); };
  $("c-mute").onclick = (e) => { muted = !muted; $("c-mute").setAttribute("aria-pressed", muted); send({ type: "mute", muted }, e.currentTarget); };
  $("c-hand").onclick = (e) => { hands = !hands; $("c-hand").setAttribute("aria-pressed", hands); send({ type: "hand_control", on: hands }, e.currentTarget); };
  $("c-lock").onclick = (e) => { if (confirm("Lock your Mac now?")) send({ type: "lock" }, e.currentTarget); };
  $("c-show").onclick = (e) => send({ type: "show" }, e.currentTarget);
  $("yes").onclick = () => { send({ type: "confirm", approved: true }); $("confirm").hidden = true; };
  $("no").onclick = () => { send({ type: "confirm", approved: false }); $("confirm").hidden = true; };

  // ── preferences ──────────────────────────────────────────────────────
  const prefs = { wake: false, speak: true };
  function loadPrefs() {
    prefs.wake = store.get("jarvis-wake") === "1";
    prefs.speak = store.get("jarvis-speak") !== "0";
    $("opt-wake").checked = prefs.wake; $("opt-speak").checked = prefs.speak;
  }
  $("btn-voice").onclick = () => { if (!SECURE && !DEMO) return openSetup(); $("vset").hidden = false; };
  $("vset-done").onclick = () => ($("vset").hidden = true);
  $("opt-speak").onchange = (e) => { prefs.speak = e.target.checked; store.set("jarvis-speak", prefs.speak ? "1" : "0");
    if (voice.mode !== "off") sendVoiceMode(voice.mode); };
  $("opt-wake").onchange = async (e) => {
    prefs.wake = e.target.checked; store.set("jarvis-wake", prefs.wake ? "1" : "0");
    if (prefs.wake) { if (await micOn()) setVoice("wake"); else { e.target.checked = prefs.wake = false; store.set("jarvis-wake", "0"); } }
    else if (voice.mode === "wake") setVoice("off");
  };

  // ── secure connection (needed for the microphone) ────────────────────
  async function probeSecure() {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 2500);
    try {
      await fetch(`https://${location.hostname}:${securePort}/remote/api/ping`, { mode: "no-cors", cache: "no-store", signal: ctl.signal });
      return true;
    } catch (e) { return false; } finally { clearTimeout(t); }
  }
  function goSecure() {
    location.href = `https://${location.hostname}:${securePort}/remote#token=${encodeURIComponent(store.get("jarvis-token") || "")}&voice=1`;
  }
  async function checkSecure(quiet) {
    try { const info = await (await fetch("/remote/api/secure", { cache: "no-store" })).json(); if (info.port) securePort = info.port; } catch (e) {}
    if (await probeSecure()) {
      const b = $("banner"); b.hidden = false;
      b.innerHTML = "<b>Voice is ready.</b> Tap to switch to the secure app, then add it to your Home Screen again.";
      b.onclick = goSecure;
      return true;
    }
    if (!quiet) { $("setup-err").textContent = "Not trusted yet. Check step 3: the switch in Certificate Trust Settings must be on."; }
    return false;
  }
  function openSetup() { $("setup-err").textContent = ""; $("setup").hidden = false; }
  $("setup-close").onclick = () => ($("setup").hidden = true);
  $("setup-go").onclick = async () => { $("setup-err").textContent = "Checking…"; if (await checkSecure(false)) goSecure(); };

  // ── voice: microphone out, his voice in ──────────────────────────────
  const voice = { mode: "off", phase: "off", capturing: false, stream: null, node: null, src: null, wakeLock: null };
  let ctx = null;
  function unlockAudio() {
    try {
      if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state !== "running") ctx.resume();
      if (navigator.audioSession) navigator.audioSession.type = voice.stream ? "play-and-record" : "playback";
    } catch (e) {}
  }
  async function micOn() {
    if (!SECURE) { openSetup(); return false; }
    unlockAudio();
    if (voice.stream && voice.stream.active) return true;
    try {
      voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    } catch (e) {
      toast(e.name === "NotAllowedError" ? "Microphone access is off. Allow it in Settings › Safari › Microphone." : "Couldn't open the microphone.", 4500);
      return false;
    }
    if (navigator.audioSession) navigator.audioSession.type = "play-and-record";
    voice.src = ctx.createMediaStreamSource(voice.stream);
    const rate = ctx.sampleRate;
    const onFrame = (f32) => pushAudio(f32, rate);
    try {
      const code = `class Tap extends AudioWorkletProcessor { process(i) { const c = i[0] && i[0][0]; if (c) this.port.postMessage(c.slice(0)); return true; } }
        registerProcessor("jarvis-tap", Tap);`;
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([code], { type: "application/javascript" })));
      voice.node = new AudioWorkletNode(ctx, "jarvis-tap");
      voice.node.port.onmessage = (m) => onFrame(m.data);
    } catch (e) {                                   // older Safari
      voice.node = ctx.createScriptProcessor(2048, 1, 1);
      voice.node.onaudioprocess = (ev) => onFrame(ev.inputBuffer.getChannelData(0).slice(0));
    }
    const mute = ctx.createGain(); mute.gain.value = 0;  // keep the graph running without echoing to the speaker
    voice.src.connect(voice.node); voice.node.connect(mute); mute.connect(ctx.destination);
    return true;
  }
  function micOff() {
    try { voice.src && voice.src.disconnect(); voice.node && voice.node.disconnect(); } catch (e) {}
    if (voice.stream) voice.stream.getTracks().forEach((t) => t.stop());
    voice.stream = voice.src = voice.node = null; voice.capturing = false;
    if (navigator.audioSession) navigator.audioSession.type = "playback";
    releaseWakeLock();
  }
  // 16 kHz, 16-bit mono, ~100 ms per message; nothing is sent while he's speaking here
  let carry = new Float32Array(0), outBuf = [];
  function pushAudio(f32, rate) {
    if (voice.mode === "off") return;
    let peak = 0; for (let i = 0; i < f32.length; i += 4) peak = Math.max(peak, Math.abs(f32[i]));
    const listening = voice.phase === "listening" || voice.phase === "hearing";
    document.body.style.setProperty("--lvl", listening ? Math.min(1, peak * 3).toFixed(2) : 0);
    if (listening && reactor) reactor.setLevel(Math.min(1, peak * 2.5), "mic");
    voice.capturing = listening;
    if (playing() || !open()) return;
    const all = new Float32Array(carry.length + f32.length); all.set(carry); all.set(f32, carry.length);
    const step = rate / 16000, n = Math.floor(all.length / step);
    const pcm = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const a = Math.floor(i * step), b = Math.min(all.length, Math.floor((i + 1) * step));
      let s = 0; for (let k = a; k < b; k++) s += all[k];
      const v = s / Math.max(1, b - a);
      pcm[i] = Math.max(-32768, Math.min(32767, v * 32767));
    }
    carry = all.slice(Math.floor(n * step));
    outBuf.push(pcm);
    if (outBuf.reduce((t, p) => t + p.length, 0) >= 1600) {
      const total = outBuf.reduce((t, p) => t + p.length, 0), joined = new Int16Array(total);
      let o = 0; outBuf.forEach((p) => { joined.set(p, o); o += p.length; }); outBuf = [];
      ws.send(joined.buffer);
    }
  }
  function sendVoiceMode(mode) { send({ type: "voice", mode, speak: prefs.speak }); }
  function setVoice(mode) {
    voice.mode = mode;
    if (mode === "off") { voice.phase = "off"; document.body.dataset.voice = "off"; $("mic").setAttribute("aria-pressed", "false"); }
    if (mode === "wake") requestWakeLock();
    sendVoiceMode(mode);
    setState(macState);
  }
  function voicePhase(e) {
    if (e.error) toast(e.error, 4500);
    voice.phase = e.phase;
    const shown = e.phase;
    document.body.dataset.voice = shown;
    $("mic").setAttribute("aria-pressed", String(shown !== "off" && shown !== "wake"));
    if (e.phase === "listening") $("state").textContent = e.follow_up ? "Go on…" : "Listening";
    if (e.phase === "transcribing") $("state").textContent = LABEL.transcribing;
    if (e.phase === "wake") setState(macState);
    if (e.phase === "off" && e.heard === false) toast("I didn't hear anything.");
    if (e.phase === "off") {
      if (prefs.wake) { voice.mode = "wake"; sendVoiceMode("wake"); }       // back to waiting for "Hey JARVIS"
      else { voice.mode = "off"; document.body.dataset.voice = "off"; setTimeout(() => { if (voice.mode === "off") micOff(); }, 400); }
    }
  }
  $("mic").onclick = async () => {
    unlockAudio();
    const busy = voice.mode === "talk" || playing();
    if (busy) {                                     // tap again: finish (and stop him talking)
      stopPlayback(); send({ type: "stop" });
      if (prefs.wake) setVoice("wake"); else { setVoice("off"); micOff(); }
      return;
    }
    if (!(await micOn())) return;
    stopPlayback();
    chime([880, 1320]);
    voice.mode = "talk"; document.body.dataset.voice = "listening"; $("mic").setAttribute("aria-pressed", "true");
    sendVoiceMode("talk");
  };
  // iOS stops the microphone when the app leaves the screen; pick up again on return
  async function voiceResume() {
    if (voice.mode === "off" || DEMO) return;
    if (ctx && ctx.state !== "running") { try { await ctx.resume(); } catch (e) {} }
    if (!voice.stream || !voice.stream.active || (ctx && ctx.state !== "running")) {
      micOff();
      if (prefs.wake) { const b = $("banner"); b.hidden = false; b.innerHTML = "<b>Tap to keep listening</b> for “Hey JARVIS”.";
        b.onclick = async () => { b.hidden = true; if (await micOn()) setVoice("wake"); }; }
      voice.mode = "off"; document.body.dataset.voice = "off";
      return;
    }
    if (voice.mode === "wake") requestWakeLock();
  }
  async function requestWakeLock() {
    try { if (navigator.wakeLock && !voice.wakeLock) { voice.wakeLock = await navigator.wakeLock.request("screen");
      voice.wakeLock.addEventListener("release", () => (voice.wakeLock = null)); } } catch (e) {}
  }
  function releaseWakeLock() { try { voice.wakeLock && voice.wakeLock.release(); } catch (e) {} voice.wakeLock = null; }

  // his voice, played here
  let audioMeta = null, playEnd = 0, sources = [];
  const playing = () => ctx && ctx.currentTime < playEnd - 0.05;
  async function audioIn(buf) {
    const meta = audioMeta; audioMeta = null;
    if (!meta) return;
    unlockAudio();
    if (!ctx) return;
    try {
      const audio = await ctx.decodeAudioData(buf.slice(0));
      const src = ctx.createBufferSource(); src.buffer = audio; src.connect(ctx.destination);
      const at = Math.max(ctx.currentTime + 0.03, playEnd);
      src.start(at); playEnd = at + audio.duration;
      sources.push(src); src.onended = () => { sources = sources.filter((s) => s !== src); };
      if (voice.mode !== "off") document.body.dataset.voice = "speaking";
    } catch (e) { console.warn("couldn't play reply", e); }
  }
  function stopPlayback() {
    sources.forEach((s) => { try { s.stop(); } catch (e) {} }); sources = []; playEnd = 0;
  }
  function chime(freqs) {
    try {
      unlockAudio(); if (!ctx) return;
      let t = ctx.currentTime + 0.01;
      freqs.forEach((f) => {
        const o = ctx.createOscillator(), g = ctx.createGain(); o.frequency.value = f;
        g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.12, t + 0.01); g.gain.linearRampToValueAtTime(0, t + 0.09);
        o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.1); t += 0.08;
      });
    } catch (e) {}
  }

  // ── demo (screenshots without a Mac) ────────────────────────────────
  function demo() {
    $("led").classList.add("up"); $("link").textContent = "Connected";
    window.__handle = handle;
    const at = (m) => new Date(Date.now() + m * 60e3).toISOString();
    handle({ type: "weather", temperature: 24, condition: "Sunny" });
    handle({ type: "agenda", events: [{ title: "Board call — Stark Industries", start: at(40), end: at(100) }] });
    handle({ type: "protocols", items: ["work_mode", "house_party_protocol", "wind_down", "good_night", "morning_protocol",
      "meeting_mode", "focus_sprint", "movie_night", "lockdown"].map((name) => ({ name })) });
    handle({ type: "transcript", text: "What's the weather tomorrow?" });
    handle({ type: "speaking", text: "Twenty-four and sunny in New Delhi tomorrow, sir, with a slight chance of rain after six.", duration: 6 });
    const q = new URLSearchParams(location.search);
    if (q.has("setup")) openSetup();
    if (q.has("vset")) $("vset").hidden = false;
    if (q.has("listening")) handle({ type: "voice", phase: "listening" });
    if (q.has("banner")) { const b = $("banner"); b.hidden = false; b.innerHTML = "<b>Voice is ready.</b> Tap to switch to the secure app, then add it to your Home Screen again."; }
  }
  start();
})();
