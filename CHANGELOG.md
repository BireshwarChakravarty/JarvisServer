# Changelog

All builds are published to the `jarvis-latest` release after passing unit tests, the in-app self-test
on Apple Silicon, a real launch, packaging, and a clean install on a fresh Mac.

Release numbers are MAJOR.MINOR from the `VERSION` file plus the number of commits since it last changed
(`scripts/version.sh`); the running version is at the top of the menu-bar menu. The 30 builds published
between 14 and 23 November 2026 count as 1.0 to 1.30; everything below "2.0" is from that series.

## 2.0: talks like a person
- **Natural voice**: Qwen3-TTS (1.7B, Apache-2.0) on the Mac, free and offline, in an original British
  voice designed for JARVIS from a written description: Butler (default), Assistant or Gentleman, chosen
  by ear from four candidates (`scripts/voice_lab.py`). Downloads once (2.3 GB); JARVIS times it on your
  Mac and uses it only if it keeps up with real-time speech, otherwise the Classic voice (Kokoro).
- **Natural replies**: Claude Sonnet by default ("Natural"; "Fast" remains); spoken replies lose assistant
  filler ("Certainly!") and say "sir" at most once; fillers are rarer; the persona gains the JARVIS
  manner. A long first sentence starts on its first clause.
- **Never cut off mid-thought**: if your words stop on "to", "and", "um" or a comma, he waits for the rest;
  longer patience once you've talked a few seconds; requests up to 60 s; listening ends even if the mic
  stops sending audio.
- **Interrupt by talking**: talk over him and he stops, keeping your words as the next request;
  "mm-hm" doesn't count; with Voice ID only your voice does.
- **Asks instead of guessing**: which John, which chat, and doubtful transcripts.
- **Memory**: "what did I ask you yesterday?"; routines noticed and anticipated.
- **Polite**: notifications instead of speech on calls and in Focus; waits for a pause; softer at night.
- **Phone anywhere** through Tailscale, and conversation history on the phone.
- **Self-check** nightly and on "run diagnostics"; reply speed measured every turn.
- **Themes** (Arc Reactor, Dot Matrix, Midnight Glass, Stark), all dark; 24-hour clock by default;
  updates always load matching scripts; version numbers in the menu bar, Settings and the installer.

# 1.x (14-23 November 2026)

## Do it for me, and fixes to voice setup, WhatsApp sending and permissions
- **Do it for me**: JARVIS clicks, types, presses keys and scrolls in any app himself (the other half of the
  on-screen guidance: that showed you where to click; now he can do it). One step at a time, checking the
  screen after each; brings the right app forward; shows the ring before each click; Send / Delete / Buy /
  Pay / Sign out and Return-in-a-form are confirmed out loud first. Self-test types into TextEdit end to end.
- **"Learn my voice" didn't wait for you**: the mic's backlog of his own instructions was taken for your
  phrase (and could become your voiceprint). Now that backlog is thrown away, a tone says "your turn", and
  he waits up to 10 seconds for each phrase.
- **WhatsApp took several tries**: Return was pressed a fixed 1.2 s after WhatsApp came forward, often
  before it had opened the chat and placed the message. Now he waits until the message is in WhatsApp's
  message box (read through Accessibility), presses Return, confirms it went (the history, or the box
  emptying) and presses once more only if the text is still sitting there. Never more than twice.
- **Permissions**: Full Disk Access has its own icon and is marked optional; Settings-only permissions say
  exactly what to do there (Full Disk Access: press + and add JARVIS), offer Relaunch and Open Settings,
  and explain the stale-entry trap (an older copy's entry shows ON but doesn't count: remove it, add again).

## Voice ID: he learns your voice and answers only you
- "Learn my voice" (or menu bar › Learn My Voice…): five spoken phrases become your voiceprint (CAM++
  speaker model from 3D-Speaker, Apache-2.0; Kaldi filterbank front end in numpy, matched to
  kaldi-native-fbank). A phrase that doesn't match the others is asked again.
- Every request is checked as it's transcribed (in parallel, no added delay). After "Hey Jarvis" a clearly
  different voice is ignored; in follow-ups (no wake word) a doubtful one is too. Too short to judge
  ("yes") always passes. Confidently-you requests are added to the profile now and then, so it keeps up
  with your voice. "Forget my voice" deletes it. The phone isn't checked (it's your paired device).
- Measured on LibriSpeech test-clean, 40 speakers: at the default 0.45, 1.5% of your requests missed and
  0.16% of other people accepted.

## WhatsApp
- "Any WhatsApp messages?", "What did Pepper say?", "Tell Happy I'll be ten minutes late." JARVIS reads the
  WhatsApp app for Mac's local chat database (a private snapshot, read-only; columns checked first, so a
  WhatsApp update that changes them is reported rather than misread) and sends through the WhatsApp app
  (`whatsapp://send`, Return), only after you confirm, then checks the database that it went.
- People first, then groups; status updates and hidden chats left out; group senders named. The morning
  greeting can lead with who's waiting on you.
- Needs Full Disk Access (new row in Settings › Permissions). No unofficial client or linked device.

## Email from Mailspring
- Email now comes from **[Mailspring](https://www.getmailspring.com)** instead of Thunderbird. He reads
  Mailspring's local database (`~/Library/Application Support/Mailspring/edgehill.db`) read-only, laid out as
  Mailspring's own sync engine defines it: every account you've added, inbox only (IMAP inbox folders and Gmail's
  inbox label), unread and not drafts, with starred, sender, subject, snippet and newsletter headers. Same top-3
  ranking. "Read me that one" uses the full text Mailspring has downloaded. "Open it" brings Mailspring to the
  front (it has no link to one message, so he says which one).
- Safe while Mailspring is syncing: a read-only connection sees only finished changes; if the database can't
  be opened in place he reads a private copy. No passwords, no plugin, no permission prompt.
- The Thunderbird reader (and its Mork index parser) is gone.

## Email from Thunderbird (replaced by Mailspring, above)
- Email now comes from **Thunderbird** instead of Apple Mail: he reads Thunderbird's local copy of your mail
  (profile from installs.ini / profiles.ini; IMAP and POP/Local inboxes; mbox or maildir), only the newest part
  of each inbox so big mailboxes stay quick. Same top-3 ranking. "Open it" shows the message in Thunderbird.
  No passwords, no add-on, no permission prompt.
- Read / flagged / deleted for IMAP accounts (Gmail included) comes from each folder's `.msf` index, read
  with a new Mork reader: Thunderbird's source shows IMAP offline copies are always written as "read" and never
  updated. Checked against Thunderbird's own test indexes and the edge cases its parser is tested on. Unread
  mail without an offline copy still shows (sender and subject).
- Setup no longer prompts for (or launches) Apple Mail.

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
