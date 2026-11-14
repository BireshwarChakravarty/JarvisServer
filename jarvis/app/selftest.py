"""End-to-end self-test, run *inside the packaged JARVIS.app* on a real Mac (CI).

  JARVIS_SELFTEST=1 JARVIS.app/Contents/MacOS/JARVIS

Checks every subsystem the way the app uses it: native frameworks load, models
download, Kokoro speaks "Hey Jarvis, what time is it in London?", the wake-word
model hears "Hey Jarvis" in that audio, Whisper transcribes it back, vision models
load, permission APIs answer without crashing, and the setup + HUD server responds.
Prints a JSON report and exits non-zero if a required check fails.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

RESULTS: list[dict] = []


def check(name: str, required: bool = True):
    def deco(fn):
        t0 = time.time()
        try:
            detail = fn()
            RESULTS.append({"check": name, "ok": True, "required": required, "detail": detail,
                            "s": round(time.time() - t0, 2)})
        except Exception as exc:
            RESULTS.append({"check": name, "ok": False, "required": required,
                            "detail": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:],
                            "s": round(time.time() - t0, 2)})
        print(("PASS " if RESULTS[-1]["ok"] else "FAIL ") + name + "  " + str(RESULTS[-1]["detail"])[:300],
              flush=True)
        return fn
    return deco


def resample(audio, sr_from: int, sr_to: int = 16000):
    import numpy as np

    n = int(len(audio) * sr_to / sr_from)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")


def run() -> int:
    home = Path(os.environ.get("JARVIS_HOME") or tempfile.mkdtemp(prefix="jarvis-selftest-"))
    os.environ["JARVIS_HOME"] = str(home)
    os.environ.setdefault("JARVIS_APP", "1")
    print(f"JARVIS self-test · python {sys.version.split()[0]} · {sys.executable}", flush=True)
    state: dict = {}

    @check("imports: core")
    def _():
        import anthropic, fastapi, uvicorn, yaml, numpy, psutil, httpx, PIL  # noqa: F401
        import jarvis.core.orchestrator, jarvis.hud.server, jarvis.tools.builtin  # noqa: F401
        from jarvis.tools.registry import registry
        return f"{len(registry)} tools, anthropic {anthropic.__version__}"

    @check("imports: native frameworks")
    def _():
        import AppKit, AVFoundation, Contacts, EventKit, Quartz, ServiceManagement, ApplicationServices  # noqa: F401
        import webview  # noqa: F401
        return "pyobjc + pywebview ok"

    @check("imports: audio & ML engines")
    def _():
        import ctranslate2, faster_whisper, kokoro_onnx, onnxruntime, openwakeword, sounddevice  # noqa: F401
        import webrtcvad  # noqa: F401
        return f"onnxruntime {onnxruntime.__version__}, ctranslate2 {ctranslate2.__version__}, " \
               f"portaudio {sounddevice.get_portaudio_version()[1]}"

    @check("imports: vision engines")
    def _():
        import cv2, mediapipe  # noqa: F401
        return f"opencv {cv2.__version__}, mediapipe {mediapipe.__version__}"

    @check("config + data dir")
    def _():
        from jarvis.config import load_config
        cfg = load_config()
        state["cfg"] = cfg
        return str(cfg.home)

    @check("model downloads (all groups)")
    def _():
        from jarvis.models import ModelManager
        mm = ModelManager(state["cfg"].models_dir, state["cfg"].get("voice.stt.model", "small.en"))
        state["mm"] = mm
        for g in mm.groups:
            if not mm.ensure(g.key):
                raise RuntimeError(f"{g.key}: {mm.progress.get(g.key)}")
        return {s["key"]: round(s["total"] / 1e6, 1) for s in mm.status()}

    @check("voice: Kokoro speaks")
    def _():
        from jarvis.voice.tts import KokoroONNX
        tts = KokoroONNX("bm_george", 1.0, state["cfg"].models_dir)
        t0 = time.time()
        audio = tts.synth("Hey Jarvis! What time is it in London?")
        state["speech"] = (audio, tts.sample_rate)
        secs = len(audio) / tts.sample_rate
        if secs < 1.0:
            raise RuntimeError(f"audio too short: {secs:.2f}s")
        return f"{secs:.1f}s of audio in {time.time() - t0:.2f}s"

    @check("voice: macOS 'say' fallback", required=False)
    def _():
        from jarvis.voice.tts import MacSay
        audio = MacSay("Daniel", 1.0).synth("Good evening, sir.")
        return f"{len(audio)} samples"

    @check("ears: wake word hears 'Hey Jarvis'")
    def _():
        import numpy as np
        from jarvis.voice.audio import FRAME, WakeWord
        ww = WakeWord("hey_jarvis", 0.5, models_dir=state["cfg"].models_dir)
        if not ww.load():
            raise RuntimeError("wake word model failed to load")
        audio, sr = state["speech"]
        pcm = (np.clip(resample(audio, sr), -1, 1) * 32767).astype(np.int16)
        pcm = np.concatenate([np.zeros(16000, np.int16), pcm, np.zeros(16000, np.int16)])
        best = 0.0
        for i in range(0, len(pcm) - FRAME, FRAME):
            scores = ww._model.predict(pcm[i:i + FRAME])
            best = max(best, max(scores.values()))
        silence = max(max(ww._model.predict(np.zeros(FRAME, np.int16)).values()) for _ in range(20))
        state["wake_score"] = best
        if best < 0.3:
            raise RuntimeError(f"peak score only {best:.2f}")
        return f"peak score {best:.2f} (silence {silence:.2f})"

    @check("ears: Whisper transcribes the speech")
    def _():
        from jarvis.voice.stt import Transcriber
        stt = Transcriber("auto", state["cfg"].get("voice.stt.model", "small.en"), "en", [],
                          models_dir=state["cfg"].models_dir)
        name = stt.load()
        audio, sr = state["speech"]
        t0 = time.time()
        text = stt.transcribe(resample(audio, sr))
        if "london" not in text.lower() or "time" not in text.lower():
            raise RuntimeError(f"unexpected transcript {text!r}")
        return f"[{name}] {text!r} in {time.time() - t0:.2f}s"

    @check("eyes: face engine")
    def _():
        import numpy as np
        from jarvis.vision.faces import FaceEngine
        fe = FaceEngine(state["cfg"].models_dir, state["cfg"].faces_dir, process_every=0)
        if not fe.load():
            raise RuntimeError("face engine failed to load")
        fe.process(np.zeros((720, 1280, 3), np.uint8))
        return "YuNet + SFace loaded"

    @check("eyes: gesture recogniser")
    def _():
        import numpy as np
        from jarvis.vision.gestures import GestureEngine
        ge = GestureEngine(state["cfg"].models_dir, lambda *a: None, fps=1000)
        if not ge.load():
            raise RuntimeError("gesture model failed to load")
        ge.process(np.zeros((480, 640, 3), np.uint8))
        return "MediaPipe gesture recogniser ran"

    @check("permissions API answers")
    def _():
        from jarvis.mac import permissions
        return {p["key"]: p["status"] for p in permissions.all_status()}

    @check("login item API answers", required=False)
    def _():
        from jarvis.mac import login_item
        return login_item.status()

    @check("keychain round-trip", required=False)
    def _():
        from jarvis.mac import keychain
        keychain.SERVICE = "JARVIS selftest"
        keychain.set_api_key("sk-test-123")
        got = keychain.get_api_key()
        keychain.delete_api_key()
        if got != "sk-test-123":
            raise RuntimeError(f"got {got!r}")
        return "ok"

    @check("setup + HUD server")
    def _():
        from fastapi.testclient import TestClient
        from jarvis.app.supervisor import Supervisor
        from jarvis.hud.server import create_app
        sup = Supervisor(state["cfg"])
        client = TestClient(create_app(supervisor=sup))
        out = {}
        for path in ("/setup", "/", "/api/setup/state", "/static/onboarding.js", "/static/reactor.js"):
            r = client.get(path)
            out[path] = r.status_code
            if r.status_code != 200:
                raise RuntimeError(f"{path} -> {r.status_code}")
        return out

    @check("brain: request builds (no network)")
    def _():
        from jarvis.core.brain import Brain, Session
        from jarvis.tools.registry import registry
        b = Brain(state["cfg"], registry, None, None, client=object())
        p = b._request_params(Session(id="x", system="s", tools=b._tool_schemas()))
        return f"{p['model']}, {len(p['tools'])} tools"

    failed = [r for r in RESULTS if not r["ok"] and r["required"]]
    report = {"ok": not failed, "results": RESULTS, "python": sys.version, "executable": sys.executable}
    out = os.environ.get("JARVIS_SELFTEST_OUT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, default=str))
    print(("\nSELFTEST PASSED" if not failed else f"\nSELFTEST FAILED: {[r['check'] for r in failed]}"), flush=True)
    return 0 if not failed else 1
