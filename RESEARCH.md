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
| **Text-to-speech** | Kokoro (best quality per parameter; MLX Metal path; British voices `bm_george`, `bm_lewis`), Piper (fast, more robotic), macOS `say` ([TTS comparison](https://tts-comparison-4hg.pages.dev/), [Piper vs Kokoro](https://www.codesota.com/text-to-speech/piper-vs-kokoro)) | **Kokoro `bm_george`** via mlx-audio, then kokoro-onnx, then `say -v Daniel` |
| **Pipeline latency** | Anthropic's [low-latency voice cookbook](https://platform.claude.com/cookbook/third-party-elevenlabs-low-latency-stt-claude-tts), [fine-grained tool streaming](https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/fine-grained-tool-streaming), [voice agent architecture](https://www.assemblyai.com/blog/voice-agent-architecture) | Stream Claude's reply, **speak each sentence as soon as it's complete**, and synthesise sentence N+1 while sentence N is playing |
| **Interruptions** | [Twilio barge-in pattern](https://www.twilio.com/en-us/blog/anthropic-conversationrelay-token-streaming-interruptions-javascript) | The wake word *while JARVIS is speaking* or an open palm ✋ stops him instantly |
| **Gestures** | [MediaPipe Gesture Recognizer](https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer/python), [small-cactus/handTrack](https://github.com/small-cactus/handTrack), [adiarora7/openpalm](https://github.com/adiarora7/openpalm) | MediaPipe with a hold-to-confirm debounce: ✋ stop, 👍/👎 answer a confirmation, ✌️ listen, 🤟 play/pause |
| **Face recognition** | dlib `face_recognition` (painful to compile on macOS), OpenCV YuNet + SFace (tiny ONNX models, CPU) | **YuNet + SFace**, with presence hysteresis so a glance away doesn't count as leaving |
| **Mac control** | AppleScript MCP servers ([adamrdrew](https://glama.ai/mcp/servers/adamrdrew/applescript-mcp), [macos-ecosystem-mcp](https://glama.ai/mcp/servers/neverprepared/macos-ecosystem-mcp), [MCP-AppleScript](https://glama.ai/mcp/servers/frouaix/MCPAppleScript)); [Orchard](https://orchard.5km.tech/apple-mcp-server) uses EventKit | Built-in tools: **EventKit** for Calendar and Reminders (inside JARVIS.app), with AppleScript as the fallback; plus Music, Messages, Notes, Shortcuts and System Events. An **MCP bridge** lets any of those servers plug in too |
| **Login and unlock detection** | launchd has no unlock trigger ([Apple forums](https://developer.apple.com/forums/thread/761467)); options are distributed notifications or polling `CGSessionCopyCurrentDictionary` | Login Item app for login; Quartz session polling for lock, unlock and sleep gaps |
| **Brain** | Claude API (tools, vision, web search), Ollama (offline) | **Claude Opus 5.5** with a streaming tool loop, server-side web search, prompt caching, and server-side fallback on refusals; Ollama when offline |

## 4. Gaps no surveyed project filled, built here

1. **A greeting that depends on the time of day and comes with a briefing.** At login, or the first time you're back at the Mac that day, you get the weather, your next meetings, overdue reminders, unread mail and headlines in under 70 spoken words. It then asks what you'd like to do today and listens without needing the wake word.
2. **Presence.** JARVIS notices when you sit down after being away and welcomes you back. Optionally it locks the Mac when you leave. In sentry mode it photographs unknown faces while you're away.
3. **Protocols.** Named routines ("house party protocol", "work mode") written in YAML. Steps can open apps, run Shortcuts (HomeKit scenes), set volume or brightness, play music, or hand a question back to the brain.
4. **Spoken confirmation for consequential actions** (sending messages, shell commands, AppleScript). You can answer by voice, with a thumbs up or down, or with the HUD button.
5. **A HUD wired to the real system**: live mic and voice levels, real CPU, memory and network figures, real calendar, the camera feed with face boxes, and every tool call as it happens.

## 5. Sources

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
