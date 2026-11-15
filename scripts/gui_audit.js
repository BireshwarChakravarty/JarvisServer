// Automated GUI audit: clipping, overflow and overlap across realistic window sizes.
//   node scripts/gui_audit.js http://127.0.0.1:7900 <outdir>
const { chromium } = require('playwright');
const base = process.argv[2], out = process.argv[3];
const SIZES = [[980, 640], [1280, 800], [1480, 920], [1512, 945], [1728, 1080], [1920, 1080], [2560, 1440], [390, 844]];
const PAGES = [
  ['hud', '/?demo', 20500],
  ['setup-welcome', '/setup?demo', 2500],
  ['setup-brain', '/setup?demo#brain', 2500],
  ['setup-senses', '/setup?demo#senses', 2500],
  ['setup-ready', '/setup?demo#ready', 2500],
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
  return issues;
};

(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  let total = 0; const report = {};
  for (const [pname, path, wait] of PAGES) {
    for (const [w, h] of SIZES) {
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
