// End-to-end: an emulated iPhone against the real companion server, with a stand-in JARVIS.
//   mkdir -p /tmp/e2e && JARVIS_HOME=/tmp/e2e python scripts/companion_e2e_server.py /tmp/e2e &
//   python scripts/companion_e2e_audio.py /tmp/e2e/say.wav
//   E2E_DIR=/tmp/e2e node scripts/companion_e2e.js
// Covers: pairing, every button acknowledged, a frozen Mac / dead link detected and recovered (the
// server is paused with SIGSTOP), taps queued while reconnecting, the voice setup, the hand-off to
// the secure connection, talking (fake microphone), his reply played on the phone, follow-ups,
// and "Hey JARVIS" failing gracefully.
const { chromium, devices } = require('playwright');
const fs = require('fs');
const { execSync } = require('child_process');
const DIR = process.env.E2E_DIR || '/tmp/e2e';
const code = fs.readFileSync(DIR + '/code', 'utf8').trim();
const events = () => fs.readFileSync(DIR + '/events.log', 'utf8');
let failures = 0;
const check = (ok, what) => { console.log((ok ? 'PASS ' : 'FAIL ') + what); if (!ok) failures++; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${DIR}/say.wav%noloop`, '--autoplay-policy=no-user-gesture-required'] });

  // ── 1. plain page: pairing, every button acknowledged ─────────────────
  const ctx = await b.newContext({ ...devices['iPhone 15 Pro'] });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(`http://127.0.0.1:7790/remote#pair=${code}`);
  await p.waitForFunction(() => document.getElementById('link').textContent === 'Connected', null, { timeout: 8000 });
  check(!(await p.url()).includes('pair='), 'pairing code removed from the address bar');
  const toastAfter = async (sel) => {
    await p.tap(sel);
    await p.waitForFunction((s) => { const el = document.querySelector(s); return !el.classList.contains('busy'); }, sel, { timeout: 7000 });
    return (await p.isVisible('#toast')) ? (await p.textContent('#toast')) : '';
  };
  check((await toastAfter('#c-stop')) === 'Stopped', 'Stop: Mac confirms "Stopped"');
  const wasMuted = (await p.getAttribute('#c-mute', 'aria-pressed')) === 'true';
  check((await toastAfter('#c-mute')) === (wasMuted ? 'Mac microphone on' : 'Mac microphone muted'), 'Mac mic: toggle confirmed');
  check((await p.getAttribute('#c-mute', 'aria-pressed')) === String(!wasMuted), 'Mac mic button shows the new state');
  check((await toastAfter('#c-hand')) === 'Hand control on', 'Hands: confirmed on');
  check((await toastAfter('#c-show')) === "JARVIS is on your Mac's screen", 'Show: confirmed');
  p.once('dialog', (d) => d.accept());
  const lockToast = await toastAfter('#c-lock');
  check(/lock|didn't work/i.test(lockToast), `Lock: answered ("${lockToast}")`);
  await p.waitForSelector('#protocols .chip');
  check((await toastAfter('#protocols .chip')) === 'Running work mode', 'Protocol chip: confirmed');
  await p.fill('#text', 'hello there'); await p.tap('#send'); await wait(600);
  check(events().includes('REQ hello there phone mac'), 'Typed request reaches JARVIS (answered on the Mac from the plain page)');

  // ── 2. a frozen Mac / dead link is noticed and recovered ──────────────
  const stop = () => execSync('pkill -STOP -f "[c]ompanion_e2e_server.py"'), cont = () => execSync('pkill -CONT -f "[c]ompanion_e2e_server.py"');
  stop();
  await p.tap('#c-stop');
  await wait(6800);
  const t1 = (await p.isVisible('#toast')) ? await p.textContent('#toast') : '';
  check(/didn't answer/.test(t1), `No answer within 6 s is reported ("${t1}")`);
  await p.tap('#c-hand');                                   // tapped while reconnecting: queued
  cont();
  await p.waitForFunction(() => document.getElementById('link').textContent === 'Connected', null, { timeout: 15000 });
  await wait(800);
  check(/hand_control", "on": false/.test(events()), 'A tap made while reconnecting is delivered afterwards');
  // back from the background with a zombie socket: wake() proves it within 2.5 s
  stop();
  await p.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await wait(3000);
  const linkText = await p.textContent('#link');
  cont();
  check(linkText !== 'Connected', `Returning to the app replaces a dead link (showed "${linkText}")`);
  await p.waitForFunction(() => document.getElementById('link').textContent === 'Connected', null, { timeout: 15000 });
  check(true, 'Reconnected after the Mac came back');

  // ── 3. voice on the plain page leads to the one-time setup ────────────
  await p.tap('#mic');
  check(await p.isVisible('#setup'), 'Mic on the plain page opens the voice setup');
  const prof = await p.request.get('http://127.0.0.1:7790/remote/jarvis.mobileconfig');
  check(prof.ok() && prof.headers()['content-type'].includes('aspen-config'), 'Profile downloads as an iOS configuration profile');
  await p.tap('#setup-go'); await wait(3200);
  check(/Not trusted yet/.test(await p.textContent('#setup-err')), 'Untrusted certificate: setup says so');
  await p.screenshot({ path: DIR + '/shot-setup.png' });
  check(errs.length === 0, 'No page errors on the plain page ' + errs.join(' | '));
  await ctx.close();

  // ── 4. certificate trusted: hand-off to the secure app, then talk ─────
  const sctx = await b.newContext({ ...devices['iPhone 15 Pro'], ignoreHTTPSErrors: true, permissions: ['microphone'] });
  const s = await sctx.newPage();
  const serrs = []; s.on('pageerror', (e) => serrs.push(e.message)); s.on('console', (m) => { if (m.type() === 'error') serrs.push(m.text()); });
  const token = await (async () => {
    const r = await s.request.post('http://127.0.0.1:7790/remote/api/pair', { data: { code, name: 'iPad' } });
    return (await r.json()).token;
  })();
  await s.goto(`http://127.0.0.1:7790/remote`);
  await s.evaluate((t) => localStorage.setItem('jarvis-token', t), token);
  await s.goto(`http://127.0.0.1:7790/remote`);
  await s.waitForSelector('#banner:not([hidden])', { timeout: 8000 });
  check(true, 'Plain page notices the trusted secure connection and offers the switch');
  await s.tap('#banner');
  await s.waitForURL(/^https:\/\/127\.0\.0\.1:7791\/remote/, { timeout: 8000 });
  await s.waitForFunction(() => document.getElementById('link').textContent === 'Connected', null, { timeout: 8000 });
  check(!s.url().includes('token='), 'Secure app connected; the key left the address bar');
  const before = events().length;
  await s.tap('#mic');
  await s.waitForFunction(() => document.body.dataset.voice === 'listening', null, { timeout: 5000 });
  check(true, 'Mic: listening');
  await s.waitForFunction(() => ['transcribing', 'thinking', 'speaking'].includes(document.body.dataset.voice), null, { timeout: 10000 });
  check(true, 'Your speech was picked up and sent');
  await s.waitForFunction(() => document.body.dataset.voice === 'speaking', null, { timeout: 10000 });
  const ev = events().slice(before);
  check(/STT (\d+)/.test(ev) && +ev.match(/STT (\d+)/)[1] > 16000 * 1.2, `Mac transcribed the whole utterance (${(ev.match(/STT (\d+)/) || [])[1]} samples)`);
  check(/REQ Test request of [\d.]+ seconds phone route/.test(ev), 'Request answered on the phone (routed)');
  check(/ROUTED True/.test(ev), 'His voice was sent to the phone');
  check((await s.textContent('#you')).startsWith('Test request'), 'What you said is shown');
  await s.waitForFunction(() => document.body.dataset.voice === 'listening', null, { timeout: 8000 });
  check(true, 'Keeps listening for a follow-up without a tap');
  await s.waitForFunction(() => document.body.dataset.voice === 'off', null, { timeout: 15000 });
  check(true, 'Quiet for a few seconds: conversation ends on its own');
  await s.screenshot({ path: DIR + '/shot-secure.png' });
  // a second conversation: the mic reopens cleanly (the fake microphone replays its recording)
  const reqs = (events().match(/^REQ /gm) || []).length;
  await s.tap('#mic');
  await s.waitForFunction(() => document.body.dataset.voice === 'listening', null, { timeout: 5000 });
  await s.waitForFunction(() => document.body.dataset.voice === 'off', null, { timeout: 25000 });
  check((events().match(/^REQ /gm) || []).length === reqs + 1, 'Second tap: the mic reopens and a second request is answered');
  check(await s.evaluate(() => !document.querySelector('#state').textContent.includes('Go on')), 'Status label restored after the conversation');
  // "Hey JARVIS" when the Mac has no detector: says so once, switches itself off, no retry loop
  await s.tap('#btn-voice');
  await s.tap('#opt-wake');
  await s.waitForFunction(() => !document.getElementById('opt-wake').checked, null, { timeout: 30000 });
  const wt = (await s.isVisible('#toast')) ? await s.textContent('#toast') : '';
  check(/detector/.test(wt), `Hey JARVIS unavailable: explained ("${wt}") and switched off`);
  const n0 = (events().match(/wake/g) || []).length;
  await wait(3000);
  check(await s.evaluate(() => document.body.dataset.voice) === 'off', 'Mic released after the failure');
  await s.tap('#vset-done');
  check(serrs.length === 0, 'No page errors in the secure app ' + serrs.join(' | '));
  await b.close();
  console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.log('CRASH', e.message); process.exit(2); });
