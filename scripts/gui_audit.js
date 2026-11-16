// Automated GUI audit: clipping, overflow and overlap across realistic window sizes.
//   node scripts/gui_audit.js http://127.0.0.1:7900 <outdir>
const { chromium } = require('playwright');
const base = process.argv[2], out = process.argv[3];
const SIZES = process.env.AUDIT_QUICK ? [[1512, 945]] : [[980, 640], [1280, 800], [1480, 920], [1512, 945], [1728, 1080], [1920, 1080], [2560, 1440], [390, 844]];
const PAGES = [
  ['hud', '/?demo', 20500],
  ['setup-welcome', '/setup?demo', 2500],
  ['setup-brain', '/setup?demo#brain', 2500],
  ['setup-senses', '/setup?demo#senses', 2500],
  ['setup-speech', '/setup?demo#speech', 2500],
  ['setup-ready', '/setup?demo#ready', 2500],
  ['mini', '/mini?demo', 3000, [[360, 96]]],   // the floating capsule has one fixed size
  ['pair', '/pair?demo', 2000],
];

const probe = () => {
  const vw = innerWidth, vh = innerHeight, issues = [];
  const visible = (el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0.05 && r.width > 0 && r.height > 0 && !el.closest('[hidden]'); };
  const name = (el) => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
  const scrollable = (el) => { for (let p = el; p; p = p.parentElement) { const o = getComputedStyle(p).overflowY; if (o === 'auto' || o === 'scroll') return p !== document.body && p !== document.documentElement; } return false; };
  // what's actually painted: the box clipped by every ancestor that hides or scrolls its overflow
  const visRect = (el) => { const r = el.getBoundingClientRect(); let [l, t, rr, b] = [r.left, r.top, r.right, r.bottom];
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const s = getComputedStyle(p); if (s.overflowX === 'visible' && s.overflowY === 'visible') continue;
      const q = p.getBoundingClientRect(); l = Math.max(l, q.left); t = Math.max(t, q.top); rr = Math.min(rr, q.right); b = Math.min(b, q.bottom); }
    return { left: l, top: t, right: rr, bottom: b, empty: rr - l < 2 || b - t < 2 }; };
  const bodyScrolls = getComputedStyle(document.body).overflowY !== 'hidden';
  const textEls = [...document.querySelectorAll('body *')].filter((el) => visible(el) && !visRect(el).empty && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
  for (const el of textEls) {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    // 1. text clipped inside its own box (ellipsis counts — it hides information)
    if ((el.scrollWidth > el.clientWidth + 2 && s.overflowX !== 'visible') || (el.scrollHeight > el.clientHeight + 2 && s.overflowY === 'hidden'))
      if (!scrollable(el)) issues.push({ kind: 'clipped-text', el: name(el), text: el.textContent.trim().slice(0, 50), w: el.clientWidth, sw: el.scrollWidth });
    // 1b. cut off by a parent that hides overflow and can't be scrolled (the activity feed fades out by design)
    { const v = visRect(el); if (!scrollable(el) && !el.closest('.activity') && ((r.bottom - v.bottom) > 3 || (v.top - r.top) > 3 || (r.right - v.right) > 3 || (v.left - r.left) > 3))
      issues.push({ kind: 'cut-by-parent', el: name(el), text: el.textContent.trim().slice(0, 40), rect: [r.left, r.top, r.right, r.bottom].map(Math.round), vis: [v.left, v.top, v.right, v.bottom].map(Math.round) }); }
    // 2. outside the window (only matters when the page can't scroll)
    if (!bodyScrolls && (r.right > vw + 1 || r.bottom > vh + 1 || r.left < -1 || r.top < -1) && !scrollable(el))
      issues.push({ kind: 'off-screen', el: name(el), text: el.textContent.trim().slice(0, 40), rect: [r.left, r.top, r.right, r.bottom].map(Math.round) });
  }
  // 3. text boxes overlapping text from a different zone
  const zoneOf = (el) => el.closest('.zone, #presence, #command, .ob-presence, .ob-panel, .dl, #island') || document.body;
  const boxes = textEls.filter((el) => !el.closest('#island')).map((el) => ({ el, r: visRect(el), z: zoneOf(el) }));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.z === b.z || a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const ix = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left), iy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (ix > 3 && iy > 3) issues.push({ kind: 'overlap', a: name(a.el), b: name(b.el), text: (a.el.textContent.trim().slice(0, 25) + ' ⟷ ' + b.el.textContent.trim().slice(0, 25)) });
  }
  // 4. canvas-drawn labels (reactor dial) out of the canvas
  const L = window.__reactorLabel;
  if (L && L.rect) {
    const [l, t, r, btm] = L.rect;
    if (l < 0 || r > vw || t < 0 || btm > vh) issues.push({ kind: 'canvas-label-off-screen', text: L.text, rect: L.rect.map(Math.round) });
    if (/…|\.\.\./.test(L.text)) issues.push({ kind: 'canvas-label-truncated', text: L.text });
    for (const x of boxes) {
      if (x.el.closest('#presence')) continue;
      const ix = Math.min(r, x.r.right) - Math.max(l, x.r.left), iy = Math.min(btm, x.r.bottom) - Math.max(t, x.r.top);
      if (ix > 2 && iy > 2) issues.push({ kind: 'canvas-label-overlap', text: L.text + ' ⟷ ' + x.el.textContent.trim().slice(0, 30) });
    }
  }
  // 5. controls: icon-only buttons need a name and a tooltip; every target ≥ 24×24 (WCAG 2.5.8)
  for (const el of document.querySelectorAll('button, a[href], [role="button"], [role="radio"], select, input:not([type="checkbox"])')) {
    if (!visible(el) || visRect(el).empty) continue;
    const r = el.getBoundingClientRect(), text = el.innerText.trim();
    const inlineLink = el.tagName === 'A' && el.closest('p, span, label') && getComputedStyle(el).display === 'inline';
    if (!text && el.querySelector('svg')) {
      if (!el.getAttribute('aria-label')) issues.push({ kind: 'icon-button-unnamed', el: name(el) });
      if (!el.title) issues.push({ kind: 'icon-button-no-tooltip', el: name(el) });
    }
    if (!inlineLink && (r.width < 24 || r.height < 24)) issues.push({ kind: 'small-target', el: name(el), size: [Math.round(r.width), Math.round(r.height)] });
  }
  // 6. one icon language per toolbar: same outline/solid mode and stroke weight (solid glyphs opt in)
  const bars = new Map();
  for (const svg of document.querySelectorAll('button svg')) {
    if (!visible(svg) || svg.dataset.icon === 'solid') continue;
    const bar = svg.closest('button').parentElement, cs = getComputedStyle(svg);
    const key = `${cs.fill === 'none' ? 'outline' : 'filled'} ${parseFloat(cs.strokeWidth).toFixed(1)}`;
    if (!bars.has(bar)) bars.set(bar, new Map());
    bars.get(bar).set(key, (bars.get(bar).get(key) || []).concat(name(svg.closest('button'))));
  }
  for (const [bar, styles] of bars) if (styles.size > 1)
    issues.push({ kind: 'mixed-icon-styles', el: name(bar), styles: [...styles].map(([k, v]) => `${k}: ${v.join(',')}`) });
  // 7. text contrast against what's actually behind it (ancestor backgrounds over the page ink)
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return [v[0], v[1], v[2], v.length > 3 ? (String(v[3]).includes('%') ? v[3] / 100 : v[3]) : 1]; };
  const lum = ([r, g, b]) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const over = (top, bottom) => [0, 1, 2].map((i) => top[i] * top[3] + bottom[i] * (1 - top[3]));
  // collect running animations once (asking per element forces a style flush every time)
  const moving = new Set(document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.target)
    .map((a) => a.effect.target));
  const memo = new Map();   // element -> [composited background, accumulated opacity]
  const backdrop = (el) => {
    if (!el) return [[3, 5, 8], 1];
    if (memo.has(el)) return memo.get(el);
    const [under, op] = backdrop(el.parentElement), s = getComputedStyle(el), c = parse(s.backgroundColor);
    const v = [c && c[3] > 0 ? over(c, under) : under, op * +s.opacity];
    memo.set(el, v); return v;
  };
  for (const el of textEls) {
    if (el.closest('[aria-hidden="true"], button:disabled, .w, .cap.said, .timeline li.past')) continue;   // decorative, disabled or intentionally de-emphasised
    let animating = false;
    for (let p = el; p && !animating; p = p.parentElement) animating = moving.has(p);
    if (animating) continue;                                       // mid fade-in: judge it once it has settled
    const cs = getComputedStyle(el); let fg = parse(cs.color); if (!fg) continue;
    const [bg, alpha] = backdrop(el);
    const text = over([fg[0], fg[1], fg[2], fg[3] * alpha], bg);
    const L1 = lum(text), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5) && el.textContent.trim().length > 1)
      issues.push({ kind: 'low-contrast', el: name(el), text: el.textContent.trim().slice(0, 30), ratio: +ratio.toFixed(2), size });
  }
  return issues;
};

(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  let total = 0; const report = {};
  for (const [pname, path, wait, sizes] of PAGES) {
    for (const [w, h] of sizes || SIZES) {
      const p = await b.newPage({ viewport: { width: w, height: h } });
      const errors = []; p.on('pageerror', (e) => errors.push(e.message));
      await p.goto(base + path, { waitUntil: 'domcontentloaded' });
      await p.waitForTimeout(wait);
      if (path.includes('#')) {
        const step = path.split('#')[1];
        await p.evaluate((st) => window.__goStep && window.__goStep(st), step);
        await p.waitForTimeout(900);
      }
      const issues = await p.evaluate(probe);
      issues.push(...errors.map((e) => ({ kind: 'js-error', text: e })));
      const key = `${pname} @ ${w}x${h}`;
      report[key] = issues; total += issues.length;
      if (issues.length) console.log(`✗ ${key}: ${issues.length}`), issues.slice(0, 12).forEach((i) => console.log('   ', JSON.stringify(i)));
      else console.log(`✓ ${key}`);
      for (let t = 0; t < 3; t++) {   // software WebGL can stall a frame; retry rather than abort the run
        try { await p.screenshot({ path: `${out}/${pname}-${w}x${h}.png`, timeout: 60000 }); break; }
        catch (e) { if (t === 2) console.log(`   (screenshot failed: ${e.message.split('\n')[0]})`); }
      }
      await p.close();
    }
  }
  require('fs').writeFileSync(`${out}/audit.json`, JSON.stringify(report, null, 1));
  console.log(`\nTOTAL ISSUES: ${total}`);
  await b.close();
})();
