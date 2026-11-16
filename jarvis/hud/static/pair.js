/* Pairing screen: shows a QR code for the companion and the devices already paired. */
(() => {
  const $ = (id) => document.getElementById(id);
  const DEMO = new URLSearchParams(location.search).has("demo");
  async function api(path, body) {
    if (DEMO) return demo(path, body);
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  let refresh = null;

  function drawQR(url) {
    const qr = qrcode(0, "M");               // auto size, medium error correction
    qr.addData(url); qr.make();
    $("qr").innerHTML = qr.createSvgTag({ cellSize: 6, margin: 0, scalable: true });
  }
  const ago = (t) => {
    const s = Date.now() / 1000 - t;
    return s < 120 ? "active now" : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
  };

  async function load() {
    clearTimeout(refresh);
    const s = await api("/api/remote").catch((e) => ({ enabled: false, error: e.message, devices: [] }));
    $("off").hidden = !!(s.enabled && s.running);
    $("on").hidden = !(s.enabled && s.running);
    $("err").textContent = s.enabled && !s.running ? (s.error || "The companion couldn't start.") : "";
    if (s.enabled && s.running) {
      drawQR(s.url);
      $("host").textContent = s.host;
      $("ipurl").textContent = s.ip_url ? s.ip_url.split("#")[0] : "";
      const mins = Math.max(1, Math.round((s.expires - Date.now() / 1000) / 60));
      $("expires").textContent = `Code valid for ${mins} more minute${mins === 1 ? "" : "s"}`;
    }
    const ul = $("devices"); ul.innerHTML = "";
    if (!s.devices.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "None yet"; ul.appendChild(li); }
    s.devices.forEach((d) => {
      const li = document.createElement("li");
      li.innerHTML = "<div><b></b><br><span></span></div>";
      li.querySelector("b").textContent = d.name; li.querySelector("span").textContent = ago(d.last_seen);
      const f = document.createElement("button"); f.className = "btn ghost small"; f.textContent = "Forget";
      f.onclick = async () => { await api("/api/remote/forget", { id: d.id }); load(); };
      li.appendChild(f); ul.appendChild(li);
    });
    refresh = setTimeout(load, 15000);   // keeps the code fresh and the device list current
  }

  $("enable").onclick = async () => {
    try { await api("/api/remote/enable", { enabled: true }); } catch (e) { $("err").textContent = e.message; }
    load();
  };
  $("disable").onclick = async () => { await api("/api/remote/enable", { enabled: false }); load(); };
  $("newcode").onclick = async () => { await api("/api/remote/new-code", {}); load(); };
  $("forgetall").onclick = async () => {
    if (confirm("Forget every paired iPhone and iPad? They'll need to scan a new code.")) { await api("/api/remote/forget", {}); load(); }
  };

  // demo data for screenshots without a running JARVIS
  function demo(path) {
    if (path === "/api/remote") return Promise.resolve({ enabled: true, running: true, host: "Tonys-Mac-mini.local",
      url: "http://Tonys-Mac-mini.local:7778/remote#pair=demo-code-0123456789", ip_url: "http://192.168.1.20:7778/remote#pair=x",
      expires: Date.now() / 1000 + 540, devices: [{ id: "a", name: "iPhone", last_seen: Date.now() / 1000 - 30 },
        { id: "b", name: "iPad", last_seen: Date.now() / 1000 - 7200 }] });
    return Promise.resolve({ ok: true });
  }
  load();
})();
