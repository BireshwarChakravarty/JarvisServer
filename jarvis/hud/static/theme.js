/* Theme switcher, loaded first on every page so the right theme is there before the first paint.
   The choice lives in JARVIS's config (sent with "hello" and as "theme" events); localStorage keeps
   it between launches so the window never flashes the wrong colours while it connects. */
(() => {
  const THEMES = ["arc", "dot", "glass", "stark"];
  const KEY = "jarvis-theme";
  const store = {
    get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) {} },
  };
  const root = document.documentElement;
  function apply(name, animate) {
    const t = THEMES.includes(name) ? name : "arc";
    if (root.dataset.theme === t) return t;
    if (animate) {
      root.classList.add("theme-switching");
      setTimeout(() => root.classList.remove("theme-switching"), 650);
    }
    root.dataset.theme = t;
    store.set(t);
    window.dispatchEvent(new CustomEvent("jarvis-theme", { detail: t }));
    return t;
  }
  const q = new URLSearchParams(location.search).get("theme");
  apply(q || store.get() || "arc", false);
  window.JarvisTheme = { THEMES, apply: (n) => apply(n, true), get: () => root.dataset.theme || "arc" };
  // another JARVIS window (Settings, the HUD) switched: follow it straight away, before the server's event lands
  window.addEventListener("storage", (e) => {
    if (e.key === KEY && e.newValue) apply(e.newValue, true);
    if (e.key === CLOCK) setClock(e.newValue === "1" ? true : e.newValue === "0" ? false : null, false);
  });

  /* 24-hour or 12-hour time, the same everywhere a time is shown. JARVIS sends the choice ("hello" and
     "prefs" events: true, false, or null to follow the language); it's kept here between launches. */
  const CLOCK = "jarvis-clock24";
  let h24 = (() => { const v = (() => { try { return localStorage.getItem(CLOCK); } catch (e) { return null; } })();
    return v === "1" ? true : v === "0" ? false : null; })();
  function setClock(v, save = true) {
    const next = v === true ? true : v === false ? false : null;
    if (save) { try { localStorage.setItem(CLOCK, next === null ? "" : next ? "1" : "0"); } catch (e) {} }
    if (next === h24) return;
    h24 = next;
    window.dispatchEvent(new CustomEvent("jarvis-clock", { detail: h24 }));
  }
  const cycle = () => (h24 === true ? { hourCycle: "h23" } : h24 === false ? { hour12: true } : {});
  window.JarvisClock = {
    set: setClock,
    get: () => h24,
    options: (o = {}) => ({ ...o, ...cycle() }),
    /* "13:05" or "1:05 PM" */
    time: (d, o = {}) => new Date(d).toLocaleTimeString([], { hour: h24 ? "2-digit" : "numeric", minute: "2-digit", ...o, ...cycle() }),
  };
})();
