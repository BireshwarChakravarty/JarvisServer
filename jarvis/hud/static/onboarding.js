/* JARVIS onboarding: every setup task behind a button. */
(function () {
  const $ = (id) => document.getElementById(id);
  const DEMO = new URLSearchParams(location.search).has("demo");
  const reactor = new Reactor($("core-gl"), $("core-ui"));
  reactor.setState("idle");

  const STEPS = ["welcome", "you", "brain", "senses", "face", "voice", "speech", "ready"];
  const CAPTIONS = {
    welcome: () => greeting() + " I'm JARVIS.",
    you: () => "First, the essentials.",
    brain: () => "Now for the clever part.",
    senses: () => "I'll ask macOS for each permission. Approve them in the dialogs.",
    face: () => "Hold still. This will only take a moment.",
    voice: () => "Go on, say my name.",
    speech: () => "I can sound however you like.",
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
  const STEP_NAMES = { welcome: "Welcome", you: "You", brain: "Intelligence", senses: "Senses", face: "Recognition",
    voice: "Listening", speech: "Voice", ready: "Ready" };
  function renderSteps() {
    $("steps").innerHTML = "";
    const n = STEPS.indexOf(step) + 1;
    $("steps").setAttribute("role", "progressbar");
    $("steps").setAttribute("aria-valuemin", "1"); $("steps").setAttribute("aria-valuemax", String(STEPS.length));
    $("steps").setAttribute("aria-valuenow", String(n));
    $("steps").setAttribute("aria-valuetext", `Step ${n} of ${STEPS.length}: ${STEP_NAMES[step] || step}`);
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
    if (s === "brain") loadBrain();
    if (s === "speech") { loadVoices(); loadLive(); }
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

  /* ───────────── brain: your Claude / ChatGPT plan, or an API key ───────────── */
  let B = null, provider = "claude-code";
  const CLI = { "claude-code": "claude", codex: "codex" };
  const NAMES = { "claude-code": ["Claude Code", "Claude"], codex: ["Codex", "ChatGPT"] };
  async function loadBrain() {
    try { B = await api("/api/setup/brain"); } catch (e) { B = { provider: "claude-code", claude: {}, codex: {}, has_key: S.has_key }; }
    provider = B.provider === "anthropic" && !B.has_key ? "claude-code" : B.provider;
    if (B.claude_model) $("claude-model").value = B.claude_model;
    if (B.speed) $("brain-speed").value = B.speed;
    else if (B.recommended_model) {   // signed in with Pro: Sonnet is quicker and easier on your allowance
      $("claude-model").value = B.recommended_model;
      api("/api/setup/brain", { provider, claude_model: B.recommended_model }).catch(() => {});
    }
    renderBrain();
  }
  function ready(p) {
    if (p === "anthropic") return !!(B && B.has_key);
    const st = B && B[CLI[p]];
    return !!(st && st.logged_in);
  }
  function renderBrain() {
    document.querySelectorAll(".plan").forEach((b) => { b.classList.toggle("on", b.dataset.p === provider); b.setAttribute("aria-checked", b.dataset.p === provider); });
    $("plan-about").textContent = {
      "claude-code": "Recommended. Uses your Claude subscription through Anthropic's official Claude Code app. No API key, no extra bills.",
      codex: "Uses your ChatGPT subscription through OpenAI's official Codex app. No API key, no extra bills.",
      anthropic: "Pay as you go with an Anthropic API key, billed by usage.",
    }[provider];
    ["claude-code", "codex", "anthropic"].forEach((p) => ($("tag-" + p).textContent = ready(p) ? "✓ ready" : ""));
    const cli = provider !== "anthropic";
    $("cli-panel").hidden = !cli; $("key-panel").hidden = cli;
    $("claude-model-field").hidden = provider !== "claude-code" || !ready("claude-code") ||
      $("brain-speed").value === "fast";                              // fast always uses the quick model
    $("speed-field").hidden = !provider;
    if (cli) {
      const st = B[CLI[provider]] || {}, [app, plan] = NAMES[provider];
      $("cs-install-t").textContent = `Install ${app}`;
      $("cs-install").classList.toggle("ok", !!st.installed);
      $("cs-install-d").textContent = st.installed ? `Installed ${st.version ? "· " + st.version : ""}` : `The official ${plan} app, one time`;
      $("cs-install-btn").hidden = !!st.installed;
      $("cs-login").classList.toggle("ok", !!st.logged_in);
      $("cs-login-d").textContent = st.logged_in ? (st.plan === false && provider === "claude-code" ? "Signed in with an API account (billed per use)" : `Signed in${st.detail ? " · " + st.detail : ""}`) : "Your browser opens to approve it";
      $("cs-login-btn").textContent = st.logged_in ? "Switch account" : "Sign in";
      $("cs-login-btn").disabled = !st.installed;
      $("cs-test-btn").disabled = !st.logged_in;
    } else {
      $("key-connected").hidden = !B.has_key; $("key-form").hidden = B.has_key;
    }
    $("key-next").textContent = cli || B.has_key ? "Continue" : "Connect";
  }
  document.querySelectorAll(".plan").forEach((b) => (b.onclick = async () => {
    provider = b.dataset.p; $("key-err").textContent = ""; renderBrain();
    api("/api/setup/brain", { provider }).catch(() => {});
  }));
  $("claude-model").onchange = () => api("/api/setup/brain", { provider, claude_model: $("claude-model").value }).catch(() => {});
  $("brain-speed").onchange = () => {
    api("/api/setup/brain", { provider, speed: $("brain-speed").value }).catch(() => {});
    $("claude-model-field").hidden = provider !== "claude-code" || !ready("claude-code") || $("brain-speed").value === "fast";
  };
  function logLine(line) {
    const el = $("cli-log"); el.hidden = false;
    el.textContent += (el.textContent ? "\n" : "") + line; el.scrollTop = el.scrollHeight;
  }
  async function cliAction(action, btn) {
    const which = CLI[provider]; $("key-err").textContent = ""; $("cli-log").textContent = "";
    busy(btn, true);
    if (action === "login") { $("cli-input-row").hidden = false; caption(`Approve the sign-in in your browser, ${S.profile.name || "sir"}.`); }
    if (action === "install") caption("Installing. This takes about a minute.");
    try {
      const r = await api(`/api/setup/brain/${which}/${action}`, {});
      B[which] = r.status;
      if (!r.ok && !(r.status && (action === "install" ? r.status.installed : r.status.logged_in))) $("key-err").textContent = `That didn't finish. ${(r.log || []).slice(-1)[0] || ""}`;
      else caption(action === "install" ? "Installed." : "Signed in. Let's make sure I can think.");
    } catch (e) { $("key-err").textContent = e.message; }
    $("cli-input-row").hidden = true; busy(btn, false); renderBrain();
    if (action === "login" && ready(provider)) {
      const fresh = await api("/api/setup/brain").catch(() => null);   // now we know the plan: Pro → Sonnet
      if (fresh && fresh.recommended_model && !$("claude-model").value) {
        $("claude-model").value = fresh.recommended_model;
        await api("/api/setup/brain", { provider, claude_model: fresh.recommended_model }).catch(() => {});
      }
      $("cs-test-btn").click();
    }
  }
  $("cs-install-btn").onclick = () => cliAction("install", $("cs-install-btn"));
  $("cs-login-btn").onclick = () => cliAction("login", $("cs-login-btn"));
  $("cli-input-send").onclick = async () => { await api(`/api/setup/brain/${CLI[provider]}/input`, { text: $("cli-input").value }).catch(() => {}); $("cli-input").value = ""; };
  $("cs-test-btn").onclick = async () => {
    busy($("cs-test-btn"), true); reactor.setState("thinking"); caption("Establishing a neural link…");
    try {
      const r = await api("/api/setup/brain/test", {});
      $("cs-test").classList.toggle("ok", r.ok);
      $("cs-test-d").textContent = r.ok ? `“${r.reply}”` : r.reply;
      reactor.setState(r.ok ? "speaking" : "error"); caption(r.ok ? r.reply : "Not quite. " + r.reply);
    } catch (e) { $("key-err").textContent = e.message; reactor.setState("error"); }
    busy($("cs-test-btn"), false); setTimeout(() => reactor.setState("idle"), 2400);
  };
  $("key-replace").onclick = () => { B.has_key = false; renderBrain(); $("key").focus(); };
  $("key-show").onclick = () => { const k = $("key"); k.type = k.type === "password" ? "text" : "password"; $("key-show").textContent = k.type === "password" ? "Show" : "Hide"; };
  $("get-key").onclick = (e) => { e.preventDefault(); api("/api/setup/open-url", { url: "https://console.anthropic.com/settings/keys" }).catch(() => {}); };
  $("key-next").onclick = async () => {
    $("key-err").textContent = "";
    if (provider !== "anthropic") {
      if (!ready(provider)) { $("key-err").textContent = `Sign in with your ${NAMES[provider][1]} plan first.`; return; }
      await api("/api/setup/brain", { provider }).catch(() => {});
      return nextStep();
    }
    if (B.has_key) { await api("/api/setup/brain", { provider }).catch(() => {}); return nextStep(); }
    const key = $("key").value.trim();
    if (!key) { $("key-err").textContent = "Paste your key first."; return; }
    busy($("key-next"), true); reactor.setState("thinking"); caption("Establishing a neural link…");
    try {
      const r = await api("/api/setup/key", { key });
      B.has_key = S.has_key = true; $("key-model").textContent = `Claude is online${r.model ? " · " + r.model : ""}. Key saved in Keychain.`;
      await api("/api/setup/brain", { provider: "anthropic" }).catch(() => {});
      renderBrain(); reactor.setState("speaking"); caption("Link established. I can think now.");
      setTimeout(() => reactor.setState("idle"), 1600);
    } catch (e) { $("key-err").textContent = e.message; reactor.setState("error"); setTimeout(() => reactor.setState("idle"), 1600); }
    busy($("key-next"), false);
  };

  /* ───────────── senses ───────────── */
  const ICON = { microphone: "🎙", camera: "📷", calendar: "📅", reminders: "☑︎", contacts: "👤", automation: "⚙︎", screen: "🖥",
                 accessibility: "⌥", full_disk: "💬" };
  // switched on only in System Settings (no dialog): what to do there, and the stale-entry trap
  const SETTINGS_ONLY = {
    accessibility: "In Accessibility, switch JARVIS on.",
    screen: "In Screen & System Audio Recording, switch JARVIS on.",
    full_disk: "In Full Disk Access, press + and choose JARVIS from Applications, then switch it on.",
  };
  const STALE = " Already on there? An older copy's entry doesn't count: select JARVIS, press −, add it again, then relaunch.";
  const LABEL = { granted: "✓ Allowed", denied: "Open Settings", not_determined: "Allow", unknown: "Allow", restricted: "Restricted", unavailable: "N/A" };
  function renderPerms(list) {
    const box = $("perms"); box.innerHTML = "";
    (list || []).forEach((p) => {
      const row = document.createElement("div");
      row.className = `perm ${p.status}`;
      row.innerHTML = `<div class="ic"></div><div><b></b><span></span></div><button class="st"></button>`;
      row.querySelector(".ic").textContent = ICON[p.key] || "•";
      row.querySelector("b").textContent = p.title;
      if (!p.required && ["screen", "accessibility", "full_disk"].includes(p.key)) {
        const o = document.createElement("em"); o.className = "opt"; o.textContent = "optional"; row.querySelector("b").appendChild(o);
      }
      row.querySelector("span").textContent = p.why;
      const btn = row.querySelector(".st");
      btn.textContent = LABEL[p.status] || "Allow";
      btn.onclick = () => requestPerm(p.key, btn);
      // Screen Recording (and sometimes Accessibility) only reach a running app after a relaunch:
      // once you've been sent to System Settings for one of them, offer that instead of "Allow"
      if (p.status !== "granted" && RELAUNCH_KEYS.includes(p.key) && asked.has(p.key)) {
        btn.textContent = "Relaunch to apply"; btn.classList.add("relaunch");
        btn.onclick = relaunch;
        row.querySelector("span").textContent = (SETTINGS_ONLY[p.key] || "") + " Then relaunch so macOS hands it over." + STALE;
        const again = document.createElement("button"); again.className = "st ghost"; again.textContent = "Open Settings";
        again.onclick = () => api(`/api/setup/permissions/${p.key}/settings`, {}).catch(() => {});
        row.appendChild(again); row.classList.add("two");
      } else if (p.status !== "granted" && SETTINGS_ONLY[p.key]) {
        row.querySelector("span").textContent = `${p.why}. ${SETTINGS_ONLY[p.key]}`;
      }
      box.appendChild(row);
    });
    const hint = $("perm-hint");
    if (S.signing === "adhoc") {
      hint.innerHTML = "Installed by dragging from the disk image, so macOS will forget these permissions with every update. "
        + "Install with the one-line installer instead and they stick.";
    } else if (S.signing === "local") {
      hint.textContent = "These stay granted across updates.";
    }
  }
  const RELAUNCH_KEYS = ["screen", "accessibility", "full_disk"];
  const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
                  del: (k) => { try { localStorage.removeItem(k); } catch (e) {} } };
  const asked = new Set(JSON.parse(store.get("jarvis-asked", "[]")));
  async function relaunch() {
    caption("Relaunching to pick up the new permissions…");
    store.set("jarvis-resume-step", step);
    await api("/api/app/restart", {}).catch(() => {});
  }
  async function refreshPerms() {
    try { S.permissions = await api("/api/setup/permissions"); renderPerms(S.permissions); } catch (e) {}
  }
  async function requestPerm(key, btn) {
    const p = S.permissions.find((x) => x.key === key);
    if (p && p.status === "granted") return;
    asked.add(key); store.set("jarvis-asked", JSON.stringify([...asked]));
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
    $("perm-hint").textContent = "Screen recording, Accessibility and Full Disk Access are optional. Turn them on later from Settings if you'd like.";
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
      ctx.strokeStyle = `rgba(${getComputedStyle(document.documentElement).getPropertyValue("--ice-rgb").trim().split(/\s+/).join(",") || "143,236,255"},0.9)`; ctx.lineWidth = 2 * dpr;
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
  $("voice-skip").onclick = nextStep;

  /* ───────────── speech: pick a voice, hear it ───────────── */
  let voiceSel = null;
  const RECOMMENDED = "butler";       // the natural voice chosen for JARVIS; Classic stays one tap away
  const sample = () => `Good evening, ${S.profile.title || "sir"}. I've run the diagnostics and everything checks out. Shall I bring the rest of the house online?`;
  async function loadVoices() {
    const v = await api("/api/setup/voices").catch(() => null);
    if (!v) return;
    voiceSel = v.current;
    const box = $("voices"); box.innerHTML = "";
    const add = (o, kind) => {
      const b = document.createElement("button");
      b.className = "voice"; b.type = "button"; b.dataset.v = o.id; b.setAttribute("role", "radio");
      b.innerHTML = `<span class="pdot"></span><div><b></b><span></span></div><i class="vplay" aria-hidden="true"></i>`;
      b.querySelector("b").textContent = o.label + (o.id === "jarvis" ? "" : "");
      b.querySelector("div span").textContent = o.about;
      if (o.id === RECOMMENDED) b.querySelector("b").insertAdjacentHTML("beforeend", ' <em class="ptag">Recommended</em>');
      if (kind === "natural") { b.dataset.sample = o.sample; b.querySelector("b").insertAdjacentHTML("beforeend", ' <em class="ptag nat">Natural</em>'); }
      if (o.id === "jarvis") b.querySelector("b").firstChild.textContent = "Classic JARVIS";
      if (kind === "neural" && !v.neural_ready) b.disabled = true;
      b.onclick = () => pickVoice(o.id);
      box.appendChild(b);
    };
    (v.natural || []).forEach((o) => add({ ...o, about: o.about }, "natural"));
    v.presets.forEach((o) => add(o, "neural"));
    markVoice();
    $("voice-hint").innerHTML = (v.natural || []).length && v.natural.some((o) => o.id === v.current) && !v.natural_ready
      ? "Downloading the natural voice (about 2 GB). I'll use the Classic voice until it's ready, then switch by myself."
      : v.natural_status && /slow/.test(v.natural_status) && !v.natural_force ? "The natural voice can't quite keep up on this Mac, so I'm using Classic. Switch it on anyway below."
      : !v.neural_ready ? "The Classic voices are still downloading; they'll be ready in a minute."
      : "";
    const nat = (v.natural || []).length > 0;
    $("natural-row").hidden = !nat;
    $("opt-natural-force").checked = !!v.natural_force;
    if (v.natural_status) $("natural-status").textContent = `Now: ${v.natural_status}.`;
  }
  function markVoice() {
    document.querySelectorAll("#voices .voice").forEach((b) => { const on = b.dataset.v === voiceSel; b.classList.toggle("on", on); b.setAttribute("aria-checked", on); });
  }
  const preview = new Audio();
  async function pickVoice(id) {
    voiceSel = id; markVoice(); $("speech-err").textContent = "";
    document.querySelectorAll("#voices .voice").forEach((b) => b.classList.toggle("playing", b.dataset.v === id));
    const card = document.querySelector(`#voices .voice[data-v="${CSS.escape(id)}"]`);
    preview.pause();
    if (card && card.dataset.sample) {        // a natural voice: hear it at once, while it downloads
      try {
        const r = await api("/api/setup/voice", { voice: id });
        reactor.setState("speaking");
        preview.src = card.dataset.sample; preview.play().catch(() => {});
        preview.onended = () => { reactor.setState("idle"); card.classList.remove("playing"); };
        if (r && r.ready === false) loadVoices();
      } catch (e) { $("speech-err").textContent = e.message; }
      return;
    }
    try {
      await api("/api/setup/voice", { voice: id });
      reactor.setState("speaking"); caption(sample()); await speak(sample());
      setTimeout(() => { reactor.setState("idle"); document.querySelectorAll("#voices .voice").forEach((b) => b.classList.remove("playing")); }, 7000);
    } catch (e) { $("speech-err").textContent = e.message; }
  }
  $("speech-next").onclick = nextStep;

  /* natural conversation: Gemini Live */
  async function loadLive() {
    const v = await api("/api/setup/live").catch(() => null);
    $("live-panel").hidden = !(v && v.installed);
    if (!v || !v.installed) return;
    $("opt-live").checked = !!(v.enabled && v.has_key);
    $("live-connected").hidden = !v.has_key;
    $("live-key-form").hidden = !!v.has_key;
    const sel = $("live-voice");
    sel.innerHTML = "";
    v.voices.forEach((o) => {
      const op = document.createElement("option");
      op.value = o.id; op.textContent = `${o.id} · ${o.about}`;
      sel.appendChild(op);
    });
    sel.value = v.voice;
    $("live-accent").value = v.accent;
    $("live-status").textContent = v.status && v.has_key ? `Now: ${v.status}.`
      : "Talk to me as you would to a person: I answer in a natural voice, hear your tone, and you can interrupt me.";
  }
  const liveSet = (patch) => api("/api/setup/live", patch).then(loadLive).catch((e) => { $("live-err").textContent = e.message; });
  $("opt-live").onchange = () => {
    $("live-err").textContent = "";
    if ($("opt-live").checked && $("live-connected").hidden) {          // no key saved yet
      $("live-err").textContent = "Add your free Gemini key first.";
      $("opt-live").checked = false;
      return;
    }
    liveSet({ enabled: $("opt-live").checked });
  };
  $("live-key-save").onclick = async () => {
    $("live-err").textContent = "";
    const key = $("live-key").value.trim();
    if (!key) { $("live-err").textContent = "Paste the key from AI Studio first."; return; }
    busy($("live-key-save"), true);
    try {
      await api("/api/setup/live-key", { key });
      $("live-key").value = "";
      await liveSet({ enabled: true });
    } catch (e) { $("live-err").textContent = e.message; }
    busy($("live-key-save"), false);
  };
  $("live-replace").onclick = () => { $("live-connected").hidden = true; $("live-key-form").hidden = false; $("live-key").focus(); };
  $("live-voice").onchange = () => liveSet({ voice: $("live-voice").value });
  $("live-accent").onchange = () => liveSet({ accent: $("live-accent").value });
  $("live-hear").onclick = () => {
    preview.pause();
    preview.src = `/static/voices/gemini/${encodeURIComponent($("live-voice").value)}.mp3`;
    preview.onerror = () => { $("live-err").textContent = "No sample for this voice yet: turn it on and say “Hey Jarvis” to hear it."; };
    preview.play().catch(() => {});
  };
  $("live-test").onclick = async () => {
    $("live-err").textContent = "";
    $("live-status").textContent = "Testing a real Gemini Live conversation…";
    busy($("live-test"), true);
    try {
      const r = await api("/api/setup/live-test", {});
      $("live-status").textContent = r.ok
        ? `Working: ${r.model}, first sound after ${r.first_sound_s} s.`
        : `Not working: ${r.error}.`;
    } catch (e) { $("live-status").textContent = `Not working: ${e.message}.`; }
    busy($("live-test"), false);
  };
  $("get-gemini-key").onclick = (e) => { e.preventDefault(); api("/api/setup/open-url", { url: "https://aistudio.google.com/apikey" }).catch(() => {}); };
  $("opt-natural-force").onchange = async () => {
    try { await api("/api/setup/natural-force", { enabled: $("opt-natural-force").checked }); } catch (e) {}
    setTimeout(loadVoices, 1500);
  };
  // Hand control: a live switch (nothing saved; it always starts off)
  const HAND_D = "Every gesture, only while this is on. Ask me any time: “hand control on”. The pointer is separate: say “air mouse”.";
  $("opt-hand").onchange = async () => {
    const on = $("opt-hand").checked;
    try {
      const r = await api("/api/setup/hand-control", { enabled: on });
      $("opt-hand").checked = !!r.enabled;
      $("hand-d").textContent = r.needs_permission
        ? "On. For the air mouse, allow JARVIS under Accessibility in the window that just opened."
        : r.enabled ? "On. Show me your hand. Say “air mouse” if you want the pointer." : HAND_D;
      caption(r.message || "");
    } catch (e) { $("opt-hand").checked = !on; caption(e.message); }
  };
  $("watch-unlock").onclick = (e) => { e.preventDefault(); e.stopPropagation(); api("/api/setup/open-unlock-settings", {}).catch(() => {}); };

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

  /* ───────────── theme picker: switches live, everywhere ───────────── */
  function markTheme(t) {
    document.querySelectorAll(".theme-card").forEach((b) => {
      const on = b.dataset.themeId === t;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    });
  }
  document.querySelectorAll(".theme-card").forEach((b) => (b.onclick = async () => {
    const t = b.dataset.themeId;
    if (window.JarvisTheme) JarvisTheme.apply(t);
    markTheme(t);
    try { await api("/api/setup/theme", { theme: t }); } catch (e) {}
  }));

  /* ───────────── clock: 24-hour or 12-hour everywhere ───────────── */
  function markClock(mode) {
    document.querySelectorAll("#clock-seg button").forEach((b) => {
      const on = b.dataset.v === mode;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    });
  }
  document.querySelectorAll("#clock-seg button").forEach((b) => (b.onclick = async () => {
    const mode = b.dataset.v;
    markClock(mode);
    if (window.JarvisClock && mode !== "auto") JarvisClock.set(mode === "24");
    try {
      const r = await api("/api/setup/clock", { mode });
      if (r && "clock24" in r && window.JarvisClock) JarvisClock.set(r.clock24);
    } catch (e) {}
  }));

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
      if (e.type === "cli_log" && e.line) logLine(e.line);
      if (e.type === "theme" && e.theme && window.JarvisTheme) { JarvisTheme.apply(e.theme); markTheme(e.theme); }
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
    $("opt-hand").checked = !!S.options.hand_control;
    $("hand-row").hidden = !settings;   // only meaningful while JARVIS is running
    if (S.theme && window.JarvisTheme) JarvisTheme.apply(S.theme);
    markTheme(S.theme || (window.JarvisTheme ? JarvisTheme.get() : "arc"));
    markClock(S.clock || "24");
    if (S.version) {
      $("build-tag").textContent = `JARVIS ${S.version}` + (S.build && S.build !== "dev" ? ` · build ${S.build}` : "");
      document.title = `JARVIS ${S.version}`;
    }
    if ("clock24" in S && window.JarvisClock) JarvisClock.set(S.clock24);
    if (settings) { $("launch").textContent = "Save & restart"; $("hello").textContent = "Settings"; }
    models = S.models; renderDownloads();
    connect();
    const resume = store.get("jarvis-resume-step", null);   // back from a permission relaunch
    store.del("jarvis-resume-step");
    const linked = location.hash.slice(1);                     // e.g. /setup#senses from the HUD
    go(resume && STEPS.includes(resume) ? resume : STEPS.includes(linked) ? linked : settings ? "you" : "welcome");
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
      face_enrolled: false, login_item: "off", app_path: "/Users/you/Downloads/JARVIS.app", in_applications: false, options: { hand_control: false } });
    if (path === "/api/setup/models") return Promise.resolve(models.map((m) => (m.ready ? m : { ...m, done: Math.min(m.total, m.done + m.total * 0.07) })));
    if (path === "/api/setup/permissions") return Promise.resolve(S.permissions);
    if (path === "/api/setup/brain") return Promise.resolve({ provider: "claude-code", has_key: false, claude_model: "",
      claude: { installed: true, version: "2.1.325 (Claude Code)", logged_in: false }, codex: { installed: false } });
    if (path === "/api/setup/devices") return Promise.resolve({ microphones: [{ name: "Studio Display Microphone", default: true }, { name: "Logitech BRIO" }],
      cameras: [{ index: 0, name: "Logitech BRIO" }, { index: 1, name: "iPhone Camera" }], input_device: null, camera_index: 0 });
    if (path === "/api/setup/voices") return Promise.resolve({ current: "butler", neural_ready: true,
      natural_ready: true, natural: [["butler", "Butler", "Refined English butler: calm, dry, quietly witty"],
        ["gentleman", "Gentleman", "Deep, warm, unflappable English gentleman"]]
        .map(([id, label, about]) => ({ id, label, about, sample: `/static/voices/${id}.mp3` })),
      presets: [["jarvis", "JARVIS", "Calm, dry British butler"], ["lewis", "Lewis", "Deeper British male"]]
        .map(([id, label, about]) => ({ id, label, about })) });
    if (path.startsWith("/api/setup/geocode")) return Promise.resolve([{ name: "London", region: "England", country: "United Kingdom", country_code: "GB" }]);
    return new Promise((res) => setTimeout(() => res({ ok: true, model: "claude-opus-5-5", best: 0.93 }), 900));
  }

  // the sticky Back/Continue bar gets its backdrop only when the step actually overflows
  const panel = document.querySelector(".ob-panel");
  const markOverflow = () => panel.classList.toggle("overflowing", panel.scrollHeight > panel.clientHeight + 2);
  new ResizeObserver(markOverflow).observe(panel);
  new MutationObserver(() => requestAnimationFrame(markOverflow)).observe(panel, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });

  if (DEMO) window.__goStep = (st) => go(st);
  init().catch((e) => caption("I couldn't reach my core: " + e.message));
})();
