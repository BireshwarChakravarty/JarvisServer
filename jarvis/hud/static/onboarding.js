/* JARVIS onboarding: every setup task behind a button. */
(function () {
  const $ = (id) => document.getElementById(id);
  const DEMO = new URLSearchParams(location.search).has("demo");
  const reactor = new Reactor($("core-gl"), $("core-ui"));
  reactor.setState("idle");

  const STEPS = ["welcome", "you", "brain", "senses", "face", "voice", "ready"];
  const CAPTIONS = {
    welcome: () => greeting() + " I'm JARVIS.",
    you: () => "First, the essentials.",
    brain: () => "Now for the clever part.",
    senses: () => "I'll ask macOS for each permission. Approve them in the dialogs.",
    face: () => "Hold still. This will only take a moment.",
    voice: () => "Go on, say my name.",
    ready: () => "Everything's in place.",
  };
  let S = null, step = "welcome", title = "sir", city = null, settings = false;

  function greeting() {
    const h = new Date().getHours();
    return h >= 5 && h < 12 ? "Good morning." : h >= 12 && h < 17 ? "Good afternoon." : "Good evening.";
  }
  function caption(text) {
    const el = $("caption");
    el.style.opacity = 0;
    setTimeout(() => { el.textContent = text; el.style.transition = "opacity .5s"; el.style.opacity = 0.92; }, 180);
  }

  /* ───────────── api ───────────── */
  async function api(path, body, method) {
    if (DEMO) return demoApi(path, body);
    const res = await fetch(path, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(data.error || data.detail || `Request failed (${res.status})`);
    return data;
  }
  const speak = (text) => api("/api/setup/speak", text ? { text } : {}).catch(() => {});

  /* ───────────── navigation ───────────── */
  function renderSteps() {
    $("steps").innerHTML = "";
    STEPS.forEach((s, i) => {
      const dot = document.createElement("i");
      const cur = STEPS.indexOf(step);
      if (i < cur) dot.className = "done";
      if (i === cur) dot.className = "on";
      if (settings) dot.onclick = () => go(s);
      $("steps").appendChild(dot);
    });
  }
  function go(next) {
    leave(step);
    step = next;
    document.querySelectorAll(".step").forEach((el) => (el.hidden = el.dataset.step !== next));
    renderSteps();
    caption(CAPTIONS[next]());
    enter(next);
  }
  const nextStep = () => go(STEPS[Math.min(STEPS.length - 1, STEPS.indexOf(step) + 1)]);
  const prevStep = () => go(STEPS[Math.max(0, STEPS.indexOf(step) - 1)]);
  document.querySelectorAll("[data-next]").forEach((b) => (b.onclick = () => { if (step === "welcome") speak(`${greeting()} I'm JARVIS. Let's get you set up.`); nextStep(); }));
  document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = prevStep));

  function busy(btn, on) { btn.disabled = on; btn.classList.toggle("busy", on); }

  /* ───────────── devices (a Mac mini has no built-in mic or camera) ───────────── */
  async function loadDevices() {
    try {
      const d = await api("/api/setup/devices");
      const mic = $("mic-select"), cam = $("cam-select");
      mic.innerHTML = ""; cam.innerHTML = "";
      if (!d.microphones.length) mic.innerHTML = "<option value=''>No microphone found: plug one in</option>";
      d.microphones.forEach((m) => {
        const o = document.createElement("option"); o.value = m.name; o.textContent = m.name + (m.default ? " (system default)" : "");
        if (d.input_device ? d.input_device === m.name : m.default) o.selected = true;
        mic.appendChild(o);
      });
      if (!d.cameras.length) cam.innerHTML = "<option value='0'>Default camera</option>";
      d.cameras.forEach((c) => {
        const o = document.createElement("option"); o.value = c.index; o.textContent = c.name;
        if (c.index === d.camera_index) o.selected = true;
        cam.appendChild(o);
      });
    } catch (e) {}
  }
  $("mic-select").onchange = () => api("/api/setup/devices", { input_device: $("mic-select").value }).catch(() => {});
  $("cam-select").onchange = async () => {
    await api("/api/setup/devices", { camera_index: Number($("cam-select").value) }).catch(() => {});
    document.querySelector(".cam").classList.remove("live");
    startCamera();
  };

  let pollTimer = null;
  function enter(s) {
    if (s === "senses") { renderPerms(S.permissions); pollTimer = setInterval(refreshPerms, 2000); }
    if (s === "face" || s === "voice") loadDevices();
    if (s === "face") startCamera();
    if (s === "brain") renderKey();
  }
  function leave(s) {
    if (s === "senses") clearInterval(pollTimer);
    if (s === "face") api("/api/setup/camera/stop", {}).catch(() => {});
  }

  /* ───────────── welcome ───────────── */
  $("hello").textContent = greeting();
  $("move-btn").onclick = async () => {
    busy($("move-btn"), true);
    try { await api("/api/setup/move-to-applications", {}); caption("Moving myself to Applications. I'll reopen in a moment."); }
    catch (e) { caption(e.message); busy($("move-btn"), false); }
  };

  /* ───────────── you ───────────── */
  document.querySelectorAll("#title-seg button").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("#title-seg button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on"); title = b.dataset.v;
  }));
  let geoTimer = null, sugg = [];
  $("city").addEventListener("input", () => {
    city = null;
    clearTimeout(geoTimer);
    const q = $("city").value.trim();
    if (q.length < 2) { $("city-suggest").hidden = true; return; }
    geoTimer = setTimeout(async () => {
      sugg = await api(`/api/setup/geocode?q=${encodeURIComponent(q)}`).catch(() => []);
      const ul = $("city-suggest"); ul.innerHTML = "";
      sugg.forEach((c, i) => {
        const li = document.createElement("li");
        li.textContent = c.name;
        const sp = document.createElement("span"); sp.textContent = [c.region, c.country].filter(Boolean).join(", ");
        li.appendChild(sp);
        li.onclick = () => { city = c; $("city").value = `${c.name}${c.country ? ", " + c.country : ""}`; ul.hidden = true; };
        ul.appendChild(li);
      });
      ul.hidden = !sugg.length;
    }, 250);
  });
  $("you-next").onclick = async () => {
    const name = $("name").value.trim();
    if (!name) { $("you-err").textContent = "I'll need a name to call you."; return; }
    $("you-err").textContent = "";
    const c = city || (sugg[0] && $("city").value.toLowerCase().startsWith(sugg[0].name.toLowerCase()) ? sugg[0] : null);
    const imperial = c && ["US", "LR", "MM"].includes(c.country_code);
    busy($("you-next"), true);
    try {
      await api("/api/setup/profile", {
        name, title: title === "__name" ? name : title,
        city: c ? c.name : ($("city").value.split(",")[0].trim() || "London"),
        latitude: c ? c.latitude : null, longitude: c ? c.longitude : null, units: imperial ? "imperial" : "metric",
      });
      S.profile.name = name;
      nextStep();
    } catch (e) { $("you-err").textContent = e.message; }
    busy($("you-next"), false);
  };

  /* ───────────── brain ───────────── */
  function renderKey() {
    $("key-connected").hidden = !S.has_key;
    $("key-form").hidden = S.has_key;
    $("key-next").textContent = S.has_key ? "Continue" : "Connect";
  }
  $("key-replace").onclick = () => { S.has_key = false; renderKey(); $("key").focus(); };
  $("key-show").onclick = () => { const k = $("key"); k.type = k.type === "password" ? "text" : "password"; $("key-show").textContent = k.type === "password" ? "Show" : "Hide"; };
  $("get-key").onclick = (e) => { e.preventDefault(); api("/api/setup/open-url", { url: "https://console.anthropic.com/settings/keys" }).catch(() => {}); };
  $("key-next").onclick = async () => {
    if (S.has_key) return nextStep();
    const key = $("key").value.trim();
    if (!key) { $("key-err").textContent = "Paste your key first."; return; }
    $("key-err").textContent = "";
    busy($("key-next"), true);
    reactor.setState("thinking"); caption("Establishing a neural link…");
    try {
      const r = await api("/api/setup/key", { key });
      S.has_key = true; $("key-model").textContent = `Claude is online${r.model ? " · " + r.model : ""}. Key saved in Keychain.`;
      renderKey(); reactor.setState("speaking"); caption("Link established. I can think now.");
      setTimeout(() => reactor.setState("idle"), 1600);
    } catch (e) { $("key-err").textContent = e.message; reactor.setState("error"); setTimeout(() => reactor.setState("idle"), 1600); }
    busy($("key-next"), false);
  };

  /* ───────────── senses ───────────── */
  const ICON = { microphone: "🎙", camera: "📷", calendar: "📅", reminders: "☑︎", contacts: "👤", automation: "⚙︎", screen: "🖥", accessibility: "⌥" };
  const LABEL = { granted: "✓ Allowed", denied: "Open Settings", not_determined: "Allow", unknown: "Allow", restricted: "Restricted", unavailable: "N/A" };
  function renderPerms(list) {
    const box = $("perms"); box.innerHTML = "";
    (list || []).forEach((p) => {
      const row = document.createElement("div");
      row.className = `perm ${p.status}`;
      row.innerHTML = `<div class="ic"></div><div><b></b><span></span></div><button class="st"></button>`;
      row.querySelector(".ic").textContent = ICON[p.key] || "•";
      row.querySelector("b").textContent = p.title;
      if (!p.required && ["screen", "accessibility"].includes(p.key)) {
        const o = document.createElement("em"); o.className = "opt"; o.textContent = "optional"; row.querySelector("b").appendChild(o);
      }
      row.querySelector("span").textContent = p.why;
      const btn = row.querySelector(".st");
      btn.textContent = LABEL[p.status] || "Allow";
      btn.onclick = () => requestPerm(p.key, btn);
      box.appendChild(row);
    });
  }
  async function refreshPerms() {
    try { S.permissions = await api("/api/setup/permissions"); renderPerms(S.permissions); } catch (e) {}
  }
  async function requestPerm(key, btn) {
    const p = S.permissions.find((x) => x.key === key);
    if (p && p.status === "granted") return;
    if (btn) busy(btn, true);
    try {
      if (p && p.status === "denied") await api(`/api/setup/permissions/${key}/settings`, {});
      else await api(`/api/setup/permissions/${key}`, {});
    } catch (e) {}
    await refreshPerms();
  }
  $("allow-all").onclick = async () => {
    busy($("allow-all"), true);
    for (const key of ["microphone", "camera", "calendar", "reminders", "contacts", "automation"]) {
      const p = S.permissions.find((x) => x.key === key);
      if (p && !["granted", "denied", "unavailable", "restricted"].includes(p.status)) await requestPerm(key);
    }
    busy($("allow-all"), false);
    const mic = S.permissions.find((x) => x.key === "microphone");
    $("perm-hint").textContent = "Screen recording and Accessibility are optional. Turn them on later from Settings if you'd like.";
    if (mic && mic.status === "granted") caption("Thank you. I can hear you now.");
  };
  let warnedMic = false;
  $("senses-next").onclick = () => {
    const mic = (S.permissions || []).find((x) => x.key === "microphone");
    if (mic && mic.status !== "granted" && mic.status !== "unavailable" && !warnedMic) {
      warnedMic = true;
      $("perm-hint").textContent = "Without the microphone I can't hear you. Press Continue again to skip anyway.";
      return;
    }
    nextStep();
  };

  /* ───────────── face ───────────── */
  async function startCamera() {
    $("face-err").textContent = "";
    try {
      const r = await api("/api/setup/camera/start", {});
      if (!r.ok) { $("face-err").textContent = "I can't reach the camera. Allow Camera in the previous step, or skip this."; return; }
      const img = $("cam");
      img.onload = () => document.querySelector(".cam").classList.add("live");
      img.src = "/camera.mjpg?t=" + Date.now();
    } catch (e) { $("face-err").textContent = e.message; }
  }
  function drawFaces(e) {
    const c = $("cam-overlay"), box = c.parentElement, dpr = window.devicePixelRatio || 1;
    c.width = box.clientWidth * dpr; c.height = box.clientHeight * dpr;
    const ctx = c.getContext("2d"); ctx.clearRect(0, 0, c.width, c.height);
    const scale = Math.max(c.width / e.w, c.height / e.h), ox = (c.width - e.w * scale) / 2, oy = (c.height - e.h * scale) / 2;
    e.faces.forEach((f) => {
      const [x, y, w, h] = f.box.map((v) => v * scale);
      ctx.strokeStyle = "rgba(143,236,255,0.9)"; ctx.lineWidth = 2 * dpr;
      ctx.beginPath(); ctx.ellipse(x + ox + w / 2, y + oy + h / 2, w * 0.62, h * 0.72, 0, 0, Math.PI * 2); ctx.stroke();
    });
  }
  $("face-go").onclick = async () => {
    busy($("face-go"), true); $("face-err").textContent = "";
    caption("Look at me, then turn your head slowly.");
    try {
      const r = await api("/api/setup/face/enroll", {});
      document.querySelector(".cam").classList.add("done");
      S.face_enrolled = true;
      caption(`Got it. I'll know you anywhere, ${S.profile.name || "sir"}.`);
      setTimeout(nextStep, 1400);
    } catch (e) { $("face-err").textContent = e.message; }
    busy($("face-go"), false);
  };
  $("face-skip").onclick = nextStep;

  /* ───────────── voice ───────────── */
  const bars = [];
  for (let i = 0; i < 48; i++) { const b = document.createElement("i"); $("bars").appendChild(b); bars.push(b); }
  let lvl = 0;
  function meter(v) { lvl = Math.max(lvl * 0.6, v); }
  setInterval(() => {
    bars.forEach((b, i) => { const w = 0.55 + 0.45 * Math.sin(i * 0.7 + Date.now() / 140); b.style.height = `${Math.max(6, lvl * 100 * w)}%`; });
    lvl *= 0.9;
  }, 60);
  $("voice-go").onclick = async () => {
    const m = document.querySelector(".meter");
    m.classList.remove("ok"); m.classList.add("live");
    $("voice-err").textContent = ""; $("meter-label").textContent = "Listening…";
    reactor.setState("listening"); busy($("voice-go"), true);
    try {
      const r = await api("/api/setup/wake-test", {});
      if (r.ok) {
        m.classList.remove("live"); m.classList.add("ok"); $("meter-label").textContent = "Heard you ✓";
        reactor.setState("speaking"); speak(); caption(`There you are, ${S.profile.name || "sir"}.`);
        setTimeout(() => { reactor.setState("idle"); nextStep(); }, 3200);
      } else {
        $("voice-err").textContent = `I didn't quite catch that (confidence ${Math.round((r.best || 0) * 100)}%). Try again, a little closer.`;
        $("meter-label").textContent = "Try again"; reactor.setState("idle"); m.classList.remove("live");
      }
    } catch (e) { $("voice-err").textContent = e.message; reactor.setState("idle"); m.classList.remove("live"); }
    busy($("voice-go"), false);
  };
  $("hear-voice").onclick = () => { reactor.setState("speaking"); speak(); setTimeout(() => reactor.setState("idle"), 3500); };

  /* ───────────── ready ───────────── */
  $("launch").onclick = async () => {
    busy($("launch"), true); $("ready-err").textContent = "";
    try {
      if (settings) {
        await api("/api/setup/login-item", { enabled: $("opt-login").checked });
        await api("/api/setup/finish", { login_item: $("opt-login").checked, auto_lock_when_away: $("opt-lock").checked, sentry_mode: $("opt-sentry").checked });
        caption("Settings saved. Restarting to apply them.");
        await api("/api/app/restart", {}).catch(() => (location.href = "/"));
        return;
      }
      await api("/api/setup/finish", { login_item: $("opt-login").checked, auto_lock_when_away: $("opt-lock").checked, sentry_mode: $("opt-sentry").checked });
      reactor.setState("working"); caption("Bringing all systems online…");
      setTimeout(() => (location.href = "/"), 2200);
    } catch (e) { $("ready-err").textContent = e.message; busy($("launch"), false); }
  };

  /* ───────────── downloads ───────────── */
  let models = [];
  const fmt = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB");
  function renderDownloads() {
    const box = $("dl-rows"); box.innerHTML = "";
    let done = 0, total = 0;
    models.forEach((m) => {
      done += m.ready ? m.total : m.done; total += m.total;
      const pct = m.ready ? 100 : Math.min(100, (m.done / Math.max(1, m.total)) * 100);
      const row = document.createElement("div");
      row.className = "dlr" + (m.ready ? " ready" : "") + (m.error ? " err" : "");
      row.innerHTML = `<div class="t"><b></b><span></span></div><div class="bar"><i style="width:${pct}%"></i></div>`;
      row.querySelector("b").textContent = m.title;
      row.querySelector("span").textContent = m.error ? "retrying…" : m.ready ? "ready" : `${Math.round(pct)}%`;
      box.appendChild(row);
    });
    $("dl-total").textContent = total ? `${fmt(done)} of ${fmt(total)}` : "";
    const all = models.length && models.every((m) => m.ready);
    $("dl").classList.toggle("done", !!all);
  }
  async function pollModels() {
    try { models = await api("/api/setup/models"); renderDownloads(); } catch (e) {}
    if (models.some((m) => m.error && !m.running)) api("/api/setup/models/retry", {}).catch(() => {});
  }
  setInterval(pollModels, 3000);

  /* ───────────── live events ───────────── */
  function connect() {
    if (DEMO) return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.type === "level" && e.source === "mic") { meter(e.value); reactor.setLevel(e.value, "mic"); }
      if (e.type === "level" && e.source === "tts") reactor.setLevel(e.value, "tts");
      if (e.type === "download") {
        const g = models.find((x) => x.key === e.group);
        if (g) { g.done = e.done; g.total = e.total || g.total; if (e.ready) g.ready = true; g.error = e.error || null; renderDownloads(); }
      }
      if (e.type === "enroll" && e.target) $("enroll-ring").style.strokeDashoffset = String(289 - (289 * e.count) / e.target);
      if (e.type === "faces" && step === "face") drawFaces(e);
    };
    ws.onclose = () => setTimeout(connect, 1500);
  }

  /* ───────────── boot ───────────── */
  async function init() {
    S = await api("/api/setup/state");
    settings = !!S.complete;
    document.body.classList.toggle("settings", settings);
    if (S.profile.name) $("name").value = S.profile.name;
    if (S.profile.city) $("city").value = S.profile.city;
    const t = S.profile.title || "sir";
    document.querySelectorAll("#title-seg button").forEach((b) => {
      const on = b.dataset.v === t || (b.dataset.v === "__name" && t === S.profile.name && t);
      b.classList.toggle("on", !!on);
      if (on) title = b.dataset.v;
    });
    $("move-banner").hidden = !(S.app_path && !S.in_applications);
    $("opt-login").checked = S.login_item ? S.login_item !== "off" || !settings : true;
    $("opt-lock").checked = !!S.options.auto_lock_when_away;
    $("opt-sentry").checked = !!S.options.sentry_mode;
    if (settings) { $("launch").textContent = "Save & restart"; $("hello").textContent = "Settings"; }
    models = S.models; renderDownloads();
    connect();
    go(settings ? "you" : "welcome");
  }

  /* ───────────── demo (/setup?demo) for previews ───────────── */
  function demoApi(path, body) {
    const perms = [["microphone", "Microphone", "Hear “Hey Jarvis” and your voice", "granted"], ["camera", "Camera", "Recognise you, read gestures, “what am I holding?”", "granted"],
      ["calendar", "Calendars", "Your agenda, briefings and new events", "not_determined"], ["reminders", "Reminders", "Create and read reminders", "not_determined"],
      ["contacts", "Contacts", "Message people by name", "denied"], ["automation", "App control", "Control apps like Music, Messages and Finder", "unknown"],
      ["screen", "Screen recording", "“What's on my screen?”", "not_determined"], ["accessibility", "Accessibility", "Hold ⌥ to talk, type text for you", "not_determined"]]
      .map(([key, t, why, status]) => ({ key, title: t, why, status, required: key === "microphone" }));
    if (path === "/api/setup/state") return Promise.resolve({ complete: false, profile: { name: "", title: "sir", city: "" }, has_key: false, permissions: perms,
      models: [{ key: "wake", title: "Wake word", total: 3.7e6, done: 3.7e6, ready: true }, { key: "ears", title: "Speech recognition", total: 4.9e8, done: 2.1e8 },
        { key: "voice", title: "Voice", total: 3.5e8, done: 3.5e8, ready: true }, { key: "eyes", title: "Vision", total: 4.7e7, done: 0 }],
      face_enrolled: false, login_item: "off", app_path: "/Users/you/Downloads/JARVIS.app", in_applications: false, options: {} });
    if (path === "/api/setup/models") return Promise.resolve(models.map((m) => (m.ready ? m : { ...m, done: Math.min(m.total, m.done + m.total * 0.07) })));
    if (path === "/api/setup/permissions") return Promise.resolve(S.permissions);
    if (path === "/api/setup/devices") return Promise.resolve({ microphones: [{ name: "Studio Display Microphone", default: true }, { name: "Logitech BRIO" }],
      cameras: [{ index: 0, name: "Logitech BRIO" }, { index: 1, name: "iPhone Camera" }], input_device: null, camera_index: 0 });
    if (path.startsWith("/api/setup/geocode")) return Promise.resolve([{ name: "London", region: "England", country: "United Kingdom", country_code: "GB" }]);
    return new Promise((res) => setTimeout(() => res({ ok: true, model: "claude-opus-5-5", best: 0.93 }), 900));
  }

  init().catch((e) => caption("I couldn't reach my core: " + e.message));
})();
