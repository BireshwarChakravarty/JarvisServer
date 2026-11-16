# Changelog

All builds are published to the `jarvis-latest` release after passing unit tests, the in-app self-test
on Apple Silicon, a real launch, packaging, and a clean install on a fresh Mac.

## Talk to JARVIS from your iPhone or iPad; companion buttons fixed
- **Companion buttons were unreliable.** Tapping *Show* ran a window call on JARVIS's own event loop that
  could freeze every command for up to 20 s, and after the phone slept iOS could leave a dead connection
  that still looked open, so taps went nowhere silently. Now: the window is shown off the event loop (also
  fixed for the mini panel, the HUD's settings link and the "show" gesture), the link is pinged and
  replaced the moment the app returns to the screen, taps made while reconnecting are delivered after,
  every command is acknowledged by the Mac (button flash + note), and a failing command can't drop the link.
- **Voice in the companion.** Tap the mic and talk: the phone streams audio to the Mac, Whisper transcribes
  it there, and his reply plays on the phone; he keeps listening for follow-ups. Optional "Hey JARVIS"
  while the companion is open, with the screen kept awake; the Mac and the phone never both answer.
- **Secure connection** for the microphone: a private certificate authority made on your Mac, installed on
  each device once as a profile, with a server certificate that renews itself.
- Transcriptions take turns (the Mac's mic and a phone can overlap; Metal speech isn't thread-safe).
- Tests: 15 new unit tests; a 30-step end-to-end test drives an emulated iPhone against the real companion
  server (including a frozen Mac and a fake microphone); the in-app self-test sends real JARVIS speech
  through the phone path over a secure link; the fresh-install check verifies the certificate, the
  profile and an acknowledgement for every command against the installed app.

## Production hardening (overnight)
- **Reliability:** core loops supervised and restarted with backoff; background tasks can't vanish
  silently; a failed reply is spoken, never silence; the plan brain retries a session it can't resume and
  stops a turn that hangs; JARVIS ignores his own voice saying "Jarvis".
- **Quit is honoured** at shutdown, restart and log-out (JARVIS could previously cancel a shutdown).
- **Memory:** voice previews reuse the one loaded voice model (was +325 MB per tap) and apply live.
- **Performance:** idle HUD renders at 30 fps; the camera stream stops while the window is hidden.
- **Visual audit:** one outline icon set (clear mute icon), WCAG AA text contrast, 24 px targets, named and
  tooltipped icon buttons; the automated audit now checks all of these, plus a 3× control contact sheet.
- **Accessibility:** setup progress announced as "Step 3 of 8"; keyboard focus rings; actionable
  "Camera unavailable · Fix in Settings".
- Lint enforced in CI.

## Permissions, hand control, greetings, protocols
- Installer signs JARVIS with a private identity created on your Mac: **permissions survive updates**.
  `--fresh` wipes everything first. **Update JARVIS…** in the menu bar (or "update yourself").
- "Relaunch to apply" for Screen Recording / Accessibility.
- Locking works without Accessibility; walk-away is judged on *you*; returning wakes the screen.
- **Hand control**: one switch for every gesture and the air mouse, off until you ask, auto-off when idle.
- Greetings change angle every time and avoid repeating themselves.
- Nine new protocols and a "More" list on the HUD.

## Real-session fixes
- Hand cursor direction (frames were mirrored twice), fingertip tracking with 1€ smoothing, air mouse.
- True-colour camera with low-light lift; recognition on its own thread.
- Whole-sentence captions synced to the voice; mini JARVIS floating capsule; local-server security guard.

## Voice, gestures, look
- JARVIS voice preset, phrase-level synthesis, spoken-form times and units, latency fillers.
- Swipes, pinch volume dial, hologram spin, attention awareness.
- Full GUI audit across 8 window sizes; captions, dial label and setup layout fixed.

## Foundation
- Native Mac app with graphical onboarding, menu bar, login item, Claude Pro/Max or ChatGPT plan as the
  brain (no API key), on-device wake word, speech recognition, voice, face recognition and gestures.
