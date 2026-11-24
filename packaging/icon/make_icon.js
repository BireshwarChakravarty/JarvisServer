// Renders packaging/icon/icon.html to packaging/icon/icon-1024.png (transparent corners).
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const b = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] });
  const p = await b.newPage({ viewport: { width: 1024, height: 1024 } });
  await p.goto('file://' + path.join(__dirname, 'icon.html') + '#a');
  await p.waitForTimeout(6000);                 // the core "ignites" for a few seconds
  await p.screenshot({ path: path.join(__dirname, 'icon-1024.png'), omitBackground: true });
  await b.close();
})();
