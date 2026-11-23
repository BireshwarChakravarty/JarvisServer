# Research notes: what already exists, and what this build takes from it

Before writing any code I surveyed the open-source "JARVIS for Mac" landscape and the
building blocks each project relies on: wake word, speech-to-text, text-to-speech,
vision, Mac automation and HUD design. No single project had everything you asked for:
a time-aware greeting at login, hands-free voice, face recognition with the camera,
deep macOS control and a cinematic interface. Most projects covered two or three of these.
This build combines the strongest idea from each into one system.

## 1. JARVIS-style assistants

| Project | What it does well | What we took |
|---|---|---|
| [saimskywalker/J.A.R.V.I.S](https://github.com/saimskywalker/J.A.R.V.I.S) (forks: [dhananjay-nautiyal](https://github.com/dhananjay-nautiyal/J.A.R.V.I.S)) | macOS voice assistant: wake word, local speech, **Kokoro TTS**, memory, reminders, **briefings**; Claude or Ollama brain | Kokoro British voice, spoken daily briefings, Ollama offline fallback |
| [bertrandmbanwi/Jarvis](https://github.com/bertrandmbanwi/Jarvis) | 109+ tools, desktop overlay, Three.js UI, OpenWakeWord "Hey JARVIS" running continuously | Large tool surface, always-on openWakeWord, overlay-style HUD |
| [huss-huss/jarvis](https://github.com/huss-huss/jarvis) | Local-first: VAD → whisper.cpp → TTS, **permission-gated** tools, long-term semantic memory | VAD endpointing pipeline, confirm-before-risky-action gating, long-term memory |
| [guiziii/Jarvis](https://github.com/guiziii/Jarvis) | Porcupine wake word → Whisper → agent → TTS | Evaluated Porcupine; picked openWakeWord because it needs no API key and ships a "hey jarvis" model |
| [cgtarmenta/jarvis](https://github.com/cgtarmenta/jarvis) | Hotkey *or* wake word → Whisper → agent CLI → Piper | Push-to-talk hotkey as a second way to trigger listening |
| [Mann473/jarvis-ai-assistant](https://github.com/Mann473/jarvis-ai-assistant), [llm-guy/jarvis](https://github.com/llm-guy/jarvis) | Fully local, no paid APIs (Ollama/Qwen) | Offline mode: drops to a local model automatically when the network is down |
| [Open-Jarvis](https://open-jarvis.com/) | Native Swift macOS agent | Ship as a real **.app bundle** so macOS permissions belong to "JARVIS", not Terminal |
| [thevickypedia/Jarvis](https://thevickypedia.github.io/Jarvis/README.html) | Mature, macOS-tested; camera and mic index config; optional face recognition | Device selection by name, camera as a first-class sensor |
| [vannu07/jarvis](https://github.com/vannu07/jarvis), [harriik/Jarvis](https://github.com/harriik/Jarvis) | Face-recognition login | Face ID drives presence ("welcome back"), the sentry mode and the HUD's target lock |
| [aniley1/JarvisAI](https://github.com/aniley1/JarvisAI) | Greets the user by name at login | Login greeting, extended to depend on the time of day and include a briefing |
| [Mycroft](https://en.wikipedia.org/wiki/Mycroft_(software)) | Was the open voice-assistant platform | Development stopped in 2023, so nothing here depends on it |

## 2. Interfaces (the "Iron Man" look)

| Source | Idea | Used how |
|---|---|---|
| [cam-hm/jarvis](https://github.com/cam-hm/jarvis) + [write-up](https://dev.to/cammanhhoang/i-built-a-free-jarvis-ai-clone-and-you-can-too-5846) | Arc reactor built from code, pulsing with Web Audio | Our reactor is 2D canvas with no dependencies. It reacts to the *real* mic and TTS levels streamed from Python |
| [jincocodev/openclaw-jarvis-ui](https://github.com/jincocodev/openclaw-jarvis-ui) | HUD dashboard around a central orb | Three-column HUD: diagnostics, atmospherics and agenda on the left; optics, activity log and protocols on the right |
| [heeelol/jester](https://github.com/heeelol/jester) | Webcam hand tracking to control holograms | Gesture control (below) |
| [Iron Man HUD breakdown](https://scifiinterfaces.com/2015/07/01/iron-man-hud-a-breakdown/) | Film UI grammar: corner brackets, tick rings, amber warnings | Face "target lock" brackets, an amber authorization modal and a boot sequence |

## 3. Building blocks: what was chosen and why

| Layer | Options weighed | Choice |
|---|---|---|
| **Wake word** | Porcupine (built-in "jarvis", needs a key; "hey jarvis" needs a custom model) vs openWakeWord (Apache-2.0, pre-trained **"hey jarvis"**, macOS/Linux/Windows) ([comparison](https://notebook-dg9.pages.dev/kb/ai/vad/wake-word-detection/), [Home Assistant's approach](https://www.home-assistant.io/voice_control/about_wake_word)) | **openWakeWord**, with a transcript-based fallback when it isn't installed |
| **Speech-to-text** | whisper.cpp (Metal), mlx-whisper (MLX), faster-whisper (CPU-only on Mac), Parakeet ([benchmarks](https://github.com/anvanvan/mac-whisper-speedtest), [comparison](https://codersera.com/blog/faster-whisper-vs-whisper-cpp-speech-to-text-2026/)) | **mlx-whisper large-v3-turbo** on Apple Silicon (GPU), **faster-whisper** on Intel |
| **Text-to-speech** | Kokoro (best quality per parameter; MLX Metal path; British voices `bm_george`, `bm_lewis`), Piper (fast, more robotic), macOS `say` ([TTS comparison](https://tts-comparison-4hg.pages.dev/), [Piper vs Kokoro](https://www.codesota.com/text-to-speech/piper-vs-kokoro)) | **Kokoro, JARVIS blend** (George + Fable) via kokoro-onnx, then the best Apple voice installed |
| **Pipeline latency** | Anthropic's [low-latency voice cookbook](https://platform.claude.com/cookbook/third-party-elevenlabs-low-latency-stt-claude-tts), [fine-grained tool streaming](https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/fine-grained-tool-streaming), [voice agent architecture](https://www.assemblyai.com/blog/voice-agent-architecture) | Stream Claude's reply, **speak each sentence as soon as it's complete**, and synthesise sentence N+1 while sentence N is playing |
| **Interruptions** | [Twilio barge-in pattern](https://www.twilio.com/en-us/blog/anthropic-conversationrelay-token-streaming-interruptions-javascript) | The wake word *while JARVIS is speaking* or an open palm ✋ stops him instantly |
| **Gestures** | [MediaPipe Gesture Recognizer](https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer/python), [small-cactus/handTrack](https://github.com/small-cactus/handTrack), [adiarora7/openpalm](https://github.com/adiarora7/openpalm) | MediaPipe with a hold-to-confirm debounce: ✋ stop, 👍/👎 answer a confirmation, ✌️ listen, 🤟 play/pause |
| **Face recognition** | dlib `face_recognition` (painful to compile on macOS), OpenCV YuNet + SFace (tiny ONNX models, CPU) | **YuNet + SFace**, with presence hysteresis so a glance away doesn't count as leaving |
| **Mac control** | AppleScript MCP servers ([adamrdrew](https://glama.ai/mcp/servers/adamrdrew/applescript-mcp), [macos-ecosystem-mcp](https://glama.ai/mcp/servers/neverprepared/macos-ecosystem-mcp), [MCP-AppleScript](https://glama.ai/mcp/servers/frouaix/MCPAppleScript)); [Orchard](https://orchard.5km.tech/apple-mcp-server) uses EventKit | Built-in tools: **EventKit** for Calendar and Reminders (inside JARVIS.app), with AppleScript as the fallback; plus Music, Messages, Notes, Shortcuts and System Events. An **MCP bridge** lets any of those servers plug in too |
| **Login and unlock detection** | launchd has no unlock trigger ([Apple forums](https://developer.apple.com/forums/thread/761467)); options are distributed notifications or polling `CGSessionCopyCurrentDictionary` | Login Item app for login; Quartz session polling for lock, unlock and sleep gaps |
| **Brain** | Claude API (tools, vision, web search), Ollama (offline) | **Claude Opus 5.5** with a streaming tool loop, server-side web search, prompt caching, and server-side fallback on refusals; Ollama when offline |

### Voice, round two: making him sound like a person

| Finding | What it changed |
|---|---|
| Kokoro's own notes grade its British male voices around C (`bm_fable`, `bm_george`) and D (`bm_lewis`, `bm_daniel`), and say quality drops on very short inputs; it is best around 100 to 200 tokens per chunk ([VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)) | Speech is now synthesised in **phrases** (up to ~220 characters) rather than single sentences, once the first sentence has started playing, so intonation no longer resets every few words |
| Kokoro voices are style vectors that can be averaged ([kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx)) | The default **JARVIS** preset blends George and Fable 50/50 at 0.94× speed: measured median pitch 131 Hz, about 175 words a minute. Every preset was checked by transcribing it with Whisper (word-perfect) |
| People pause longer after a full stop or question than after a comma | Breaths of 120 to 320 ms by punctuation, and 6 ms fades so chunk joins never click |
| TTS reads "9:00 AM" and "e.g." literally | A spoken-form pass for the voice only (subtitles keep the written form): "nine tomorrow morning", "half past seven", "for example", units, no emoji |
| Silence while a tool runs feels broken in a voice UI ([voice agent design](https://www.assemblyai.com/blog/voice-agent-architecture)) | If no words arrive within 1.3 s he says "One moment, sir." (varied, never repeated twice running), and a second line after 9 s |
| Apple's downloadable Premium voices are far better than the compact defaults ([Apple: Spoken Content voices](https://support.apple.com/guide/mac-help/change-the-voice-your-mac-uses-to-speak-text-mchlp2290/mac)) | The `say` fallback picks the best installed British voice (Jamie Premium first), and setup offers a link to download one |
| Newer open models (e.g. [Kyutai Pocket TTS](https://huggingface.co/kyutai/pocket-tts)) sound more natural but need PyTorch and a gated download whose terms forbid cloning a voice without consent | Not bundled. JARVIS will never clone the film actor's voice; the blend is an original voice |

### Gestures, round two: motion, not just poses

| Finding | What it changed |
|---|---|
| MediaPipe's recogniser classifies only static poses, but it also returns 21 hand landmarks per frame ([Gesture Recognizer](https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer/python), [hand landmarks](https://developers.google.com/mediapipe/solutions/vision/hand_landmarker)) | `jarvis/vision/motion.py` tracks the palm centre and the thumb-to-index gap over time: swipes, flicks and pinch-and-drag, all from the same model, with no extra download |
| Air-gesture projects ([mctrl](https://github.com/manav1702/mctrl), [handTrack](https://github.com/small-cactus/handTrack)) use pinch as the "grab", with hysteresis so it doesn't flicker | Pinch opens at a gap under 0.32 palm widths and releases above 0.50, after two frames; a drag locks to one axis after 3.5% of travel |
| A swipe performed with an open hand looks like ✋ "stop" to a pose classifier | Held poses only count while the hand is still; letting go of a pinch is never read as a swipe |
| The film's workshop holograms are grabbed and turned by hand | Pinch and drag sideways spins the HUD core with inertia; pinch and drag vertically is a volume dial drawn around the core |
| Face landmarks give head yaw cheaply (nose against the eye midpoint) | An attention estimate with hysteresis: the HUD's periphery rests when you look away |

### Round three: what your first real session showed

| Finding | What it changed |
|---|---|
| The camera already mirrors frames like a selfie; the motion code mirrored them again, so the hand cursor ran the wrong way and swipes were reversed | Fixed at the source, with tests that use real camera orientation. The cursor now follows the index fingertip through an interaction box, smoothed with the [1€ filter](https://gery.casiez.net/1euro/) (as air-mouse projects such as [slastra/pointer](https://github.com/slastra/pointer) do), and recognition runs at 24 fps while a hand is in view |
| Recognition ran on the capture thread, so slow frames queued up and the cursor trailed your hand | Capture and recognition now run on separate threads; recognition always takes the newest frame and skips stale ones |
| The camera preview looked black and white: the HUD desaturated it, twice | True colour, plus a gamma lift for dim rooms on the preview only (recognition sees the raw frame) |
| Captions trimmed old words off the front, so the text started mid-sentence. Broadcast guidance ([BBC subtitle guidelines](https://clevercast.com/bbc-subtitling-guidelines)): about two lines, break at sentence and phrase boundaries | Whole sentences only: the current one bright, the previous one dimmed, revealed as the voice reaches them |
| A floating PiP needs a window that is non-activating, joins all Spaces and floats over full-screen apps ([Apple developer forums](https://developer.apple.com/forums/thread/826308), [floating panel patterns](https://cindori.com/developer/floating-panel)); pywebview 6 exposes `on_top`, `transparent`, `frameless`, `focus=False` and the native `NSWindow` | The mini capsule: canJoinAllSpaces + fullScreenAuxiliary + stationary, status-window level, `setCanHide_(False)` so ⌘H leaves it up, click-through while the air mouse is on |
| Unlocking from a webcam: macOS blocks synthetic input on the lock screen, the sanctioned route is an authorization plug-in ([Apple developer forums](https://developer.apple.com/forums/thread/62687)), and 2D face matching is fooled by a photo ([liveness research](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4327091/)) | No password typing. On return he wakes the display for [Apple Watch unlock](https://support.apple.com/en-us/HT206995), Touch ID or your password, then briefs you once you're in |
| Security review: the local server didn't check `Origin` or `Host`, so any web page could open its WebSocket and send commands (cross-site WebSocket hijacking), or read it via DNS rebinding | A guard on every request: localhost-only Host, same-origin Origin on WebSocket and POSTs, Fetch-Metadata refusal of cross-site requests (including camera embeds) |

## 4. Gaps no surveyed project filled, built here

1. **A greeting that depends on the time of day and comes with a briefing.** At login, or the first time you're back at the Mac that day, you get the weather, your next meetings, overdue reminders, unread mail and headlines in under 70 spoken words. It then asks what you'd like to do today and listens without needing the wake word.
2. **Presence.** JARVIS notices when you sit down after being away and welcomes you back. Optionally it locks the Mac when you leave. In sentry mode it photographs unknown faces while you're away.
3. **Protocols.** Named routines ("house party protocol", "work mode") written in YAML. Steps can open apps, run Shortcuts (HomeKit scenes), set volume or brightness, play music, or hand a question back to the brain.
4. **Spoken confirmation for consequential actions** (sending messages, shell commands, AppleScript). You can answer by voice, with a thumbs up or down, or with the HUD button.
5. **A HUD wired to the real system**: live mic and voice levels, real CPU, memory and network figures, real calendar, the camera feed with face boxes, and every tool call as it happens.

## 5. Visual design

The HUD's look was researched separately: what the film HUDs' designers (Jayse Hansen, Territory Studio) said about their process, Apple's visionOS glass guidelines, and modern voice-orb patterns. See [DESIGN.md](DESIGN.md).

## 6. Packaging as a real Mac app

| Question | Finding | Decision |
|---|---|---|
| How do you ship a Python app as a `.app`? | PyInstaller, py2app and [Briefcase](https://briefcase.beeware.org/en/latest/reference/platforms/macOS/index.html) all work. py2app traces imports and misses dynamically loaded code ([Glyph](https://blog.glyph.im/2023/03/py-mac-app-for-real.html)). Briefcase writes privacy usage strings and entitlements from config and builds a DMG. | **Briefcase**, Apple Silicon only (`universal_build = false`), macOS 14+ (onnxruntime and PyAV wheels need it). |
| Which engines can be bundled? | `mlx-whisper` pulls in PyTorch and `mlx-audio` pulls in transformers. `faster-whisper` (CTranslate2), `kokoro-onnx` (bundles espeak-ng) and openWakeWord (ONNX) all ship self-contained arm64 wheels. Checked: all 96 resolved dependencies have Apple Silicon wheels except `proxy-tools`, which we ship as a pre-built wheel. | faster-whisper `small.en` + Kokoro ONNX + openWakeWord. Models are downloaded to Application Support, so the signed bundle is never modified. Since 2.0 the bundle also carries `mlx` + `mlx-audio` for the natural voice (Qwen3-TTS); current mlx-audio needs `transformers` but not PyTorch, about 150 MB more. `mlx-whisper` stays out: it still pulls in PyTorch. |
| Camera and mic prompts | macOS attributes a prompt to the responsible signed app and needs `NS…UsageDescription` keys, or it silently denies. | Usage strings and entitlements in the bundle. Onboarding requests each permission natively (AVFoundation, EventKit, Contacts, AX, CoreGraphics) and shows live status. |
| Gatekeeper | Sequoia removed the Control-click "Open" override; unnotarized downloads need **System Settings › Open Anyway** ([MacRumors](https://www.macrumors.com/2024/08/06/macos-sequoia-gatekeeper-security-change/), [9to5Mac](https://9to5mac.com/2024/08/13/security-bite-apple-finally-making-it-harder-to-override-gatekeeper-is-a-telling-move/)). Files fetched with `curl` carry no quarantine flag. | Ad-hoc-signed DMG on GitHub Releases, plus a one-line `curl` installer that avoids the prompt. |
| Launch at login | `SMAppService.mainApp` (macOS 13+) registers the app itself as a login item ([Apple](https://developer.apple.com/documentation/servicemanagement/smappservice/mainapp)). | A toggle in onboarding, with a System Events fallback. |
| Background behaviour | pywebview lets a `closing` handler cancel the close by returning False; its Cocoa delegate has no Dock-reopen hook. | Closing hides the window. A Dock-reopen handler is added at runtime, plus a menu-bar item. ⌘Q still quits. |

## 7. Sources

- https://github.com/saimskywalker/J.A.R.V.I.S
- https://github.com/dhananjay-nautiyal/J.A.R.V.I.S
- https://github.com/guiziii/Jarvis
- https://github.com/bertrandmbanwi/Jarvis
- https://github.com/huss-huss/jarvis
- https://github.com/Mann473/jarvis-ai-assistant
- https://github.com/cgtarmenta/jarvis
- https://github.com/llm-guy/jarvis
- https://open-jarvis.com/
- https://en.wikipedia.org/wiki/Mycroft_(software)
- https://github.com/vannu07/jarvis · https://github.com/harriik/Jarvis · https://github.com/aniley1/JarvisAI
- https://thevickypedia.github.io/Jarvis/README.html
- https://notebook-dg9.pages.dev/kb/ai/vad/wake-word-detection/
- https://www.home-assistant.io/voice_control/about_wake_word
- https://picovoice.ai/platform/porcupine
- https://tts-comparison-4hg.pages.dev/ · https://www.codesota.com/text-to-speech/piper-vs-kokoro · https://www.promptquorum.com/power-local-llm/best-local-tts-mac
- https://github.com/anvanvan/mac-whisper-speedtest · https://codersera.com/blog/faster-whisper-vs-whisper-cpp-speech-to-text-2026/ · https://simonwillison.net/2024/Aug/13/mlx-whisper
- https://platform.claude.com/cookbook/third-party-elevenlabs-low-latency-stt-claude-tts
- https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/fine-grained-tool-streaming
- https://www.assemblyai.com/blog/voice-agent-architecture
- https://www.twilio.com/en-us/blog/anthropic-conversationrelay-token-streaming-interruptions-javascript
- https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer/python
- https://github.com/small-cactus/handTrack · https://github.com/adiarora7/openpalm
- https://github.com/cam-hm/jarvis · https://dev.to/cammanhhoang/i-built-a-free-jarvis-ai-clone-and-you-can-too-5846
- https://github.com/jincocodev/openclaw-jarvis-ui · https://github.com/heeelol/jester
- https://scifiinterfaces.com/2015/07/01/iron-man-hud-a-breakdown/
- https://glama.ai/mcp/servers/adamrdrew/applescript-mcp · https://glama.ai/mcp/servers/neverprepared/macos-ecosystem-mcp · https://glama.ai/mcp/servers/frouaix/MCPAppleScript · https://orchard.5km.tech/apple-mcp-server
- https://developer.apple.com/forums/thread/761467 · https://pythonguis.com/faq/launching-pyqt-apps-in-macos-at-login
- https://github.com/gabrimatic/kokoro-mlx · https://github.com/thewh1teagle/kokoro-onnx
