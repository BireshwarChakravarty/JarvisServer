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
})();
