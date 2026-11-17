# Changelog

All builds are published to the `jarvis-latest` release after passing unit tests, the in-app self-test
on Apple Silicon, a real launch, packaging, and a clean install on a fresh Mac.

## Gestures fixed: ✌️ listening, and the air mouse no longer hijacks your hand
- **✌️ (and the HUD mic) stopped listening at once**: the start chime was picked up by the mic, taken for
  speech, transcribed as nothing, and the conversation ended. Now the chime's audio is discarded, and a
  noise that turns out to be no words keeps him listening for the rest of the window.
- **The air mouse is a mode you ask for** ("air mouse", "air mouse off"): it used to switch itself on
  whenever your hand looked like ☝️ for half a second (often, by accident), then owned the hand: ✋, 👍,
  ✌️, swipes and the volume dial stopped working and pinches became clicks. When it ends, gestures are
  back, and the ✋ that ended it doesn't also fire "stop".

## Faster replies, like talking to a person
- **Instant answers** for everyday commands (time, date, timers, pause / next song, volume, mute, lock):
  done on the Mac in milliseconds, no AI round trip; his brain is told what he did on the next turn.
- **Fast by default**: a quick model at low effort (Claude Code: Haiku, effort low; API: Haiku; ChatGPT:
  low reasoning effort), with Balanced and Thorough in Settings › Intelligence › Replies. Claude Code
  skips loading memory files and bundled skills on every turn.
- **Sooner first words**: a long opening sentence is spoken from its first natural pause; obvious lookups
  get "Checking your calendar." at 0.7 s instead of 1.3 s.
- **Faster transcription**: no timestamps and no second voice-detection pass on short turns.
- **Smarter end of turn**: answer at once when Smart Turn is confident you've finished (≥ 0.7), wait the
  normal pause when it's unsure, and up to 2 s only when you're clearly mid-thought (tuned on the Mac
  self-test, where a cut-off phrase scored 0.59).

## "Show me how" on-screen guidance; a more natural conversation
- **On-screen guidance.** "How do I…?" → he finds the button or menu (Accessibility, with Apple Vision text
  recognition as the fallback), flies a light to it, lassos it with a caption and says why, waits for your
  click, then the next step. Walkthroughs are remembered and help fades: full, then hints, then "you've got
  this". A transparent, click-through layer above every app, on whichever display holds the target.
- **He knows when you've finished.** Smart Turn v3 (open model, 8 MB, ~25 ms) judges the end of your turn from
  your voice instead of a fixed silence: quick replies to short answers, no cutting off mid-thought pauses.
  Features computed in numpy and checked against Whisper's reference implementation.
- **"Sir?" instead of a beep** when you say his name and pause (pre-rendered, so it's instant; never spoken
  over you, and never transcribed as your words).
- **Fillers that fit** ("Checking your calendar.", "Looking through your inbox.") and never repeat back to back
  (fixed a bug where lines containing your title could).
- **Conversation style**: spoken examples instead of adjectives in his instructions, and he's told how his
  last replies opened so he varies them.

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
