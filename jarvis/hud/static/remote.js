/* JARVIS companion (iPhone / iPad). Pairs once from the Mac's QR code, then keeps a live link. */
(() => {
  const $ = (id) => document.getElementById(id);
  const DEMO = new URLSearchParams(location.search).has("demo");
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
                  del: (k) => { try { localStorage.removeItem(k); } catch (e) {} } };
  const LABEL = { idle: "Standby", listening: "Listening", transcribing: "Hearing you", thinking: "Thinking",
    working: "On it", speaking: "Speaking", error: "Fault", muted: "Mic muted", booting: "Starting" };
  let reactor = null, ws = null, backoff = 1000, muted = false, hands = false;

  function deviceName() {
    const ua = navigator.userAgent;
    if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
    if (/iPhone/.test(ua)) return "iPhone";
    return "Browser";
  }
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 3500);
  }

  // ── pairing ──────────────────────────────────────────────────────────
  async function start() {
    const code = (location.hash.match(/pair=([\w-]+)/) || [])[1];
    if (code && !DEMO) {
      history.replaceState(null, "", location.pathname);       // the code never stays in the address bar
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
    DEMO ? demo() : connect();
  }

  // ── link ─────────────────────────────────────────────────────────────
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/remote/ws?token=${encodeURIComponent(store.get("jarvis-token") || "")}`);
    ws.onopen = () => { backoff = 1000; $("led").classList.add("up"); $("link").textContent = "Connected"; };
    ws.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
    ws.onclose = (e) => {
      $("led").classList.remove("up");
      if (e.code === 4401) {          // this device was forgotten on the Mac
        store.del("jarvis-token"); $("app").hidden = true; $("unpaired").hidden = false;
        $("pair-err").textContent = "This device was unpaired. Scan a new code to pair again."; return;
      }
      $("link").textContent = "Reconnecting…";
      setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15000);
    };
  }
  const send = (msg) => { if (DEMO) return; if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); else toast("Not connected to your Mac"); };
  document.addEventListener("visibilitychange", () => {   // iOS suspends background pages: reconnect on return
    if (!document.hidden && !DEMO && (!ws || ws.readyState > 1)) connect();
  });

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
    document.body.dataset.state = s; reactor && reactor.setState(s);
    $("state").textContent = muted && s === "idle" ? LABEL.muted : LABEL[s] || s;
  }
  function handle(e) {
    switch (e.type) {
      case "hello": setState(e.state || "idle"); break;
      case "state": setState(e.state); break;
      case "level": if (reactor && (e.source === "tts" || document.body.dataset.state === "listening")) reactor.setLevel(e.value, e.source); break;
      case "transcript": if (!e.replay) { $("you").textContent = e.text; $("jarvis").textContent = ""; } break;
      case "speaking": if (!e.replay) { lastSpoke = Date.now(); subtitle(e.text, e.duration); } break;
      case "reply": if (!e.replay && Date.now() - lastSpoke > 30000) subtitle(e.text); break;
      case "confirm": $("confirm-q").textContent = e.action; $("confirm").hidden = false; break;
      case "confirm_result": $("confirm").hidden = true; break;
      case "mic": muted = !!e.muted; $("c-mute").setAttribute("aria-pressed", muted); setState(document.body.dataset.state); break;
      case "hand_control": hands = !!e.on; $("c-hand").setAttribute("aria-pressed", hands); break;
      case "announce": if (!e.replay) toast(e.text); break;
      case "error": if (!e.replay) toast(e.message); break;
      case "weather": if (e.temperature != null) $("wx").textContent = `${Math.round(e.temperature)}° ${e.condition || ""}`; break;
      case "agenda": nextUp(e.events || []); break;
      case "protocols": protocols(e.items || []); break;
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
      b.onclick = () => { send({ type: "protocol", name: p.name }); toast(`Running ${b.textContent}`); };
      box.appendChild(b);
    });
  }

  // ── controls ─────────────────────────────────────────────────────────
  $("ask").onsubmit = (ev) => {
    ev.preventDefault();
    const t = $("text").value.trim(); if (!t) return;
    send({ type: "text", text: t }); $("text").value = ""; $("text").blur();
    $("you").textContent = t;
  };
  $("c-stop").onclick = () => send({ type: "stop" });
  $("c-mute").onclick = () => send({ type: "mute", muted: !muted });
  $("c-hand").onclick = () => send({ type: "hand_control", on: !hands });
  $("c-lock").onclick = () => { if (confirm("Lock your Mac now?")) send({ type: "lock" }); };
  $("c-show").onclick = () => { send({ type: "show" }); toast("JARVIS is on your Mac's screen"); };
  $("yes").onclick = () => { send({ type: "confirm", approved: true }); $("confirm").hidden = true; };
  $("no").onclick = () => { send({ type: "confirm", approved: false }); $("confirm").hidden = true; };

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
  }
  start();
})();
