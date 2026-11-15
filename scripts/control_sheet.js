// Visual review aid: every interactive control on every screen, captured at 3× next to its
// accessible name, as one contact sheet per screen. Geometry checks can't tell whether an icon is
// legible; this makes each one big enough for a person (or a reviewer) to judge.
//   node scripts/control_sheet.js http://127.0.0.1:7900 <outdir>
const { chromium } = require('playwright');
const fs = require('fs');
const base = process.argv[2], out = process.argv[3];
const PAGES = [
  ['hud', '/?demo&gesture', 4000, async (p) => { await p.evaluate(() => window.__handle({ type: 'hand_control', on: true })); }],
  ['hud-muted', '/?demo&gesture', 4000, async (p) => { await p.evaluate(() => window.__handle({ type: 'mic', muted: true })); }],
  ['setup-brain', '/setup?demo', 2000, async (p) => { await p.evaluate(() => window.__goStep('brain')); }],
  ['setup-senses', '/setup?demo', 2000, async (p) => { await p.evaluate(() => window.__goStep('senses')); }],
  ['setup-speech', '/setup?demo', 2000, async (p) => { await p.evaluate(() => window.__goStep('speech')); }],
  ['setup-ready', '/setup?demo', 2000, async (p) => { await p.evaluate(() => window.__goStep('ready')); }],
];

(async () => {
  const b = await chromium.launch();
  const index = [];
  for (const [name, path, wait, prep] of PAGES) {
    const p = await b.newPage({ viewport: { width: 1512, height: 945 }, deviceScaleFactor: 3 });
    await p.goto(base + path); await p.waitForTimeout(wait);
    if (prep) await prep(p);
    await p.waitForTimeout(900);
    const els = await p.evaluate(() => {
      const vis = (el) => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('[hidden]'); };
      return [...document.querySelectorAll('button, a[href], input, select, label.tg, .chip, [role="radio"]')]
        .filter(vis).map((el, i) => {
          el.setAttribute('data-sheet', i);
          const r = el.getBoundingClientRect();
          return { i, tag: el.tagName.toLowerCase(), id: el.id, cls: el.className && typeof el.className === 'string' ? el.className : '',
            name: el.getAttribute('aria-label') || el.title || el.innerText.trim().slice(0, 50) || el.placeholder || '(no name)',
            w: Math.round(r.width), h: Math.round(r.height), iconOnly: !el.innerText.trim() && !!el.querySelector('svg') };
        });
    });
    let html = `<html><body style="background:#202530;color:#dde;font:13px -apple-system;margin:20px"><h2>${name}</h2>` +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px">';
    for (const e of els) {
      const file = `${name}-${e.i}.png`;
      try { await p.locator(`[data-sheet="${e.i}"]`).screenshot({ path: `${out}/${file}`, timeout: 15000 }); } catch (err) { continue; }
      html += `<figure style="margin:0;padding:10px;background:#0b0f15;border-radius:10px"><img src="${file}" style="max-width:100%;max-height:140px;image-rendering:auto"><figcaption>` +
        `<b>${e.name}</b><br><small>${e.tag}${e.id ? '#' + e.id : ''} · ${e.w}×${e.h}${e.iconOnly ? ' · icon-only' : ''}</small></figcaption></figure>`;
    }
    html += '</div></body></html>';
    fs.writeFileSync(`${out}/${name}.html`, html);
    fs.writeFileSync(`${out}/${name}.json`, JSON.stringify(els.filter((e) => fs.existsSync(`${out}/${name}-${e.i}.png`))));
    index.push([name, els.length]);
    await p.close();
  }
  console.log(index.map(([n, c]) => `${n}: ${c} controls`).join('\n'));
  await b.close();
})();
