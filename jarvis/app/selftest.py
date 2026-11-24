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


_LOG = None


def log(line: str) -> None:
    print(line, flush=True)
    if _LOG:
        with open(_LOG, "a") as fh:
            fh.write(line + "\n")


def check(name: str, required: bool = True, timeout: float = 180):
    """Run a check in a worker thread so a hang becomes a named failure, not a stuck build."""
    import threading

    def deco(fn):
        t0 = time.time()
        log(f"…    {name}")
        box: dict = {}

        def target():
            try:
                box["detail"] = fn()
            except BaseException as exc:
                box["exc"] = exc
                box["trace"] = traceback.format_exc()[-1500:]

        th = threading.Thread(target=target, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            res = {"check": name, "ok": False, "required": required, "detail": f"timed out after {timeout:.0f}s"}
        elif "exc" in box:
            exc = box["exc"]
            res = {"check": name, "ok": False, "required": required, "detail": f"{type(exc).__name__}: {exc}",
                   "trace": box["trace"]}
        else:
            res = {"check": name, "ok": True, "required": required, "detail": box.get("detail")}
        res["s"] = round(time.time() - t0, 2)
        RESULTS.append(res)
        log(("PASS " if res["ok"] else ("FAIL " if required else "WARN ")) + name + "  " + str(res["detail"])[:300])
        if not res["ok"] and res.get("trace"):
            log(res["trace"])
        return fn
    return deco


def resample(audio, sr_from: int, sr_to: int = 16000):
    import numpy as np

    n = int(len(audio) * sr_to / sr_from)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")


def run() -> int:
    global _LOG
    _LOG = os.environ.get("JARVIS_SELFTEST_LOG")
    home = Path(os.environ.get("JARVIS_HOME") or tempfile.mkdtemp(prefix="jarvis-selftest-"))
    os.environ["JARVIS_HOME"] = str(home)
    os.environ.setdefault("JARVIS_APP", "1")
    log(f"JARVIS self-test · python {sys.version.split()[0]} · {sys.executable}")
    state: dict = {}

    @check("imports: core")
    def _():
        import anthropic
        import PIL  # noqa: F401

        import jarvis.core.orchestrator
        import jarvis.hud.server
        import jarvis.tools.builtin  # noqa: F401
        from jarvis.tools.registry import registry
        return f"{len(registry)} tools, anthropic {anthropic.__version__}"

    @check("imports: native frameworks")
    def _():
        import ApplicationServices  # noqa: F401
        import webview  # noqa: F401
        return "pyobjc + pywebview ok"

    @check("imports: audio & ML engines")
    def _():
        import ctranslate2
        import onnxruntime
        import sounddevice
        import webrtcvad  # noqa: F401
        return f"onnxruntime {onnxruntime.__version__}, ctranslate2 {ctranslate2.__version__}, " \
               f"portaudio {sounddevice.get_portaudio_version()[1]}"

    @check("imports: vision engines")
    def _():
        import cv2
        import mediapipe
        return f"opencv {cv2.__version__}, mediapipe {mediapipe.__version__}"

    @check("multiprocessing helpers don't relaunch the app", timeout=60)
    def _():
        import multiprocessing
        from multiprocessing import resource_tracker

        import psutil
        lock = multiprocessing.RLock()  # what tqdm does: starts the resource tracker helper
        time.sleep(2)
        pid = getattr(resource_tracker._resource_tracker, "_pid", None)
        if pid is None:
            return "no resource tracker needed"
        proc = psutil.Process(pid)
        cmd = " ".join(proc.cmdline())
        if "resource_tracker" not in cmd:
            raise RuntimeError(f"helper process isn't a resource tracker: {cmd[:200]}")
        kids = [p for p in psutil.Process().children(recursive=True) if p.pid != pid]
        selftests = [p for p in kids if "JARVIS" in " ".join(p.cmdline()) and p.pid != pid]
        del lock
        return f"resource tracker pid {pid} ok; other JARVIS children: {len(selftests)}"

    @check("config + data dir")
    def _():
        from jarvis.config import load_config
        cfg = load_config()
        state["cfg"] = cfg
        return str(cfg.home)

    @check("model downloads (all groups)", timeout=1200)
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

    @check("natural voice: Qwen3-TTS speaks in the Butler voice", required=False, timeout=900)
    def _():
        # WARN-only: GitHub's virtual Mac is far slower than a real one, so speed is reported, not judged
        from jarvis.voice import qwen_tts as q
        if not q.supported():
            raise RuntimeError("MLX not available")
        nv = q.QwenVoice("butler", state["cfg"].models_dir, max_rtf=99)
        try:
            rtf = nv.load()
            t0 = time.time()
            audio = nv.synth("Good evening, sir. Everything checks out.")
            secs = len(audio) / nv.sample_rate
            if secs < 1.0:
                raise RuntimeError(f"audio too short: {secs:.2f}s")
            return f"{secs:.1f}s of audio in {time.time() - t0:.1f}s; {rtf:.2f}x real time on this machine"
        finally:
            nv.close()

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

    @check("ears: Whisper transcribes the speech", timeout=300)
    def _():
        from jarvis.voice.stt import Transcriber
        stt = Transcriber("auto", state["cfg"].get("voice.stt.model", "small.en"), "en", [],
                          models_dir=state["cfg"].models_dir)
        name = stt.load()
        name = f"{name} on the {stt.device_used.upper() if stt.device_used == 'gpu' else 'CPU'}"
        audio, sr = state["speech"]
        t0 = time.time()
        text = stt.transcribe(resample(audio, sr))
        if "london" not in text.lower() or "time" not in text.lower():
            raise RuntimeError(f"unexpected transcript {text!r}")
        state["stt"] = stt
        return f"[{name}] {text!r} in {time.time() - t0:.2f}s"

    @check("voice: the JARVIS blend speaks clearly, in spoken form", timeout=300)
    def _():
        from jarvis.voice.text import speech_form
        from jarvis.voice.tts import VOICE_PRESETS, KokoroONNX
        p = VOICE_PRESETS["jarvis"]
        tts = KokoroONNX("jarvis", p["speed"], state["cfg"].models_dir)
        line = speech_form("Your board call is at 9:00 AM tomorrow, sir.")
        audio = tts.synth(line)
        text = state["stt"].transcribe(resample(audio, tts.sample_rate)).lower()
        if ("nine" not in text and "9" not in text) or "morning" not in text:
            raise RuntimeError(f"said {line!r}, heard {text!r}")
        return f"said {line!r}; Whisper heard {text!r}"

    @check("ears: Smart Turn hears a finished question vs a sentence cut off mid-thought", timeout=240)
    def _():
        from jarvis.voice.tts import KokoroONNX
        from jarvis.voice.turn import TurnDetector
        det = TurnDetector(state["cfg"].models_dir)
        if not det.load():
            raise RuntimeError(f"model didn't load from {det.path}")
        tts = KokoroONNX("bm_george", 1.0, state["cfg"].models_dir)
        done = resample(tts.synth("What time is it in London?"), tts.sample_rate)
        cut = resample(tts.synth("Could you set a timer for"), tts.sample_rate)
        t0 = time.time()
        p_done, p_cut = det.probability(done), det.probability(cut)
        ms = (time.time() - t0) * 500
        state["turn"] = (p_done, p_cut)
        if p_done is None or p_cut is None:
            raise RuntimeError("no verdict")
        return f"finished question p={p_done:.2f} · cut-off phrase p={p_cut:.2f} · {ms:.0f} ms each"

    @check("ears: Smart Turn ranks the finished question above the cut-off one", required=False)
    def _():
        p_done, p_cut = state["turn"]
        if not p_done > p_cut:
            raise RuntimeError(f"finished {p_done:.2f} ≤ cut-off {p_cut:.2f}")
        return f"{p_done:.2f} > {p_cut:.2f}"

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

    @check("eyes: motion gestures (swipe, pinch dial)")
    def _():
        from jarvis.vision.gestures import GestureEngine

        def hand(x, y, pinch=False):
            pts = [(x, y)] * 21
            pts[0], pts[9], pts[4] = (x, y + 0.05), (x, y - 0.05), (x - 0.01, y - 0.08)
            pts[8] = (x - 0.01, y - 0.08) if pinch else (x + 0.07, y - 0.12)
            return pts
        got, moves = [], []
        ge = GestureEngine(state["cfg"].models_dir, lambda g, a: got.append(a), on_motion=moves.append)
        for i in range(6):   # the camera mirrors frames: image-right is the user's right
            ge.handle_motion(i / 12, hand(0.30 + 0.06 * i, 0.5))
        frames = [hand(0.5, 0.6, True)] * 3 + [hand(0.5, 0.6 - 0.03 * i, True) for i in range(1, 7)] + [None]
        for i, f in enumerate(frames):
            ge.handle_motion(5 + i / 12, f)
        kinds = [m.kind for m in moves]
        if got != ["next"] or kinds[0] != "pinch_start" or kinds[-1] != "pinch_end" or "pinch_drag" not in kinds:
            raise RuntimeError(f"swipes {got}, motion {kinds}")
        return "swipe → next track; pinch → drag → release"

    @check("permissions API answers", timeout=30)
    def _():
        from jarvis.mac import permissions
        return {p["key"]: p["status"] for p in permissions.all_status()}

    @check("login item API answers", required=False, timeout=20)
    def _():
        from jarvis.mac import login_item
        return login_item.status()

    @check("keychain round-trip", required=False, timeout=20)
    def _():
        if os.environ.get("CI"):
            return "skipped on CI (keychain may prompt)"
        from jarvis.mac import keychain
        keychain.SERVICE = "JARVIS selftest"
        keychain.set_api_key("sk-test-123")
        got = keychain.get_api_key()
        keychain.delete_api_key()
        if got != "sk-test-123":
            raise RuntimeError(f"got {got!r}")
        return "ok"

    @check("mini JARVIS: the floating-panel APIs it uses exist")
    def _():
        import AppKit
        import Quartz
        names = ["NSWindowCollectionBehaviorCanJoinAllSpaces", "NSWindowCollectionBehaviorFullScreenAuxiliary",
                 "NSWindowCollectionBehaviorStationary", "NSWindowCollectionBehaviorIgnoresCycle",
                 "NSStatusWindowLevel", "NSApplicationDidHideNotification", "NSApplicationDidUnhideNotification"]
        missing = [n for n in names if not hasattr(AppKit, n)]
        missing += [n for n in ("CGEventCreateMouseEvent", "CGEventPost", "kCGEventMouseMoved", "kCGEventLeftMouseDown",
                                "kCGEventLeftMouseUp", "kCGEventLeftMouseDragged", "kCGHIDEventTap") if not hasattr(Quartz, n)]
        if missing:
            raise RuntimeError(f"missing: {missing}")
        from jarvis.app.mini import MiniPanel  # noqa: F401
        from jarvis.vision.airmouse import MacPointer  # noqa: F401
        vf = AppKit.NSScreen.mainScreen().visibleFrame() if AppKit.NSScreen.mainScreen() else None
        return f"AppKit + Quartz symbols present; visible frame {vf.size.width:.0f}x{vf.size.height:.0f}" if vf else "symbols present"

    @check("guidance: Accessibility reads a real app's interface (Finder's menu bar)", timeout=60)
    def _():
        import ApplicationServices as AS
        from AppKit import NSRunningApplication

        from jarvis.mac import screen_elements as se
        if not AS.AXIsProcessTrusted():
            return "skipped: Accessibility not granted to this process"
        finder = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")
        if not finder:
            raise RuntimeError("Finder isn't running")
        els = se.ax_elements(int(finder[0].processIdentifier()), "Finder")
        menus = [e for e in els if e.role == "AXMenuBarItem"]
        top = se.rank(els, "the Go menu")
        if not menus or not top or top[0][0].label != "Go":
            raise RuntimeError(f"{len(els)} elements, menus={[m.label for m in menus][:8]}, top={top[:1]}")
        g = top[0][0]
        if g.w < 5 or g.h < 5:
            raise RuntimeError(f"'Go' has no usable frame: {g}")
        return f"{len(els)} elements · 'Go' menu at ({g.x:.0f}, {g.y:.0f}) {g.w:.0f}×{g.h:.0f}"

    @check("guidance: Vision reads text and locates it (fallback for apps without Accessibility)", timeout=60)
    def _():
        import tempfile

        from PIL import Image, ImageDraw, ImageFont

        from jarvis.mac import screen_elements as se
        img = Image.new("RGB", (900, 300), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        except Exception:
            font = ImageFont.load_default()
        d.text((60, 60), "Export as PDF", fill="black", font=font)
        d.text((520, 200), "AutoSum", fill="black", font=font)
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/t.png"
            img.save(path)
            found = se.recognise_text(path)
        texts = {t.lower(): b for t, b, _ in found}
        hit = next((b for t, b in texts.items() if "autosum" in t), None)
        if hit is None:
            raise RuntimeError(f"read {list(texts)}")
        x, y, w, h = se.vision_box_to_global(hit, (0, 0, 900, 300))
        if not (480 <= x <= 560 and 180 <= y <= 230):
            raise RuntimeError(f"'AutoSum' placed at ({x:.0f}, {y:.0f}), expected near (520, 200)")
        return f"read {sorted(texts)} · 'AutoSum' at ({x:.0f}, {y:.0f})"

    @check("lock: the permission-free lock call is available (not called)")
    def _():
        import ctypes
        login = ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login")
        if not hasattr(login, "SACLockScreenImmediate"):
            raise RuntimeError("SACLockScreenImmediate missing: locking would fall back to ⌃⌘Q")
        return "SACLockScreenImmediate present"

    @check("shutdown: JARVIS never refuses to quit (restart, shut down, log out)")
    def _():
        import Foundation
        from webview.platforms import cocoa

        from jarvis.app import menubar
        from jarvis.app.runner import allow_app_termination
        allow_app_termination()
        delegate = cocoa.BrowserView.AppDelegate.alloc().init()
        answer = delegate.applicationShouldTerminate_(None)
        if answer != Foundation.YES or not menubar.is_quit_shortcut():
            raise RuntimeError(f"applicationShouldTerminate answered {answer!r}: macOS would cancel shutdown")
        menubar._terminating = False
        return "applicationShouldTerminate → YES"

    @check("companion: the iPhone/iPad listener starts, serves the app, refuses unpaired devices", timeout=60)
    def _():
        import asyncio
        import socket

        import httpx

        from jarvis.app.supervisor import Supervisor
        sup = Supervisor(state["cfg"])
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]

        async def go():
            ok = await sup.remote.start(host="127.0.0.1", port=port)
            try:
                async with httpx.AsyncClient() as c:
                    page = await c.get(f"http://127.0.0.1:{port}/remote")
                    pair = await c.post(f"http://127.0.0.1:{port}/remote/api/pair", json={"code": "guess"})
                return ok, page.status_code, "remote.js" in page.text, pair.status_code
            finally:
                await sup.remote.stop()
        ok, page, has_js, pair = asyncio.run(go())
        if not (ok and page == 200 and has_js and pair == 403):
            raise RuntimeError(f"started={ok} page={page} js={has_js} pair-with-bad-code={pair}")
        return f"listener on :{port} · page 200 · bad pairing code refused (403)"

    @check("companion voice: secure link a phone can trust, every tap acknowledged, speech heard, reply sent back",
           timeout=240)
    def _():
        import asyncio
        import json
        import socket
        import ssl

        import numpy as np
        import websockets

        from jarvis.app.supervisor import Supervisor
        stt = state["stt"]
        heard_requests = []

        class StandIn:                                   # the real phone path, real Whisper, no brain
            ctx, wake = None, None

            def __init__(self):
                self.stt = stt

            async def hud_command(self, msg):
                pass

            def claim_voice(self, who, hold=4.0):
                return True

            def queue_request(self, text, source="protocol", route=None, done=None):
                heard_requests.append((text, source, route))
                if done is not None:
                    done.set_result(True)

        sup = Supervisor(state["cfg"])
        sup.jarvis = StandIn()
        ports = []
        for _ in range(2):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0)); ports.append(s.getsockname()[1])
        audio, sr = state["speech"]
        pcm16 = (np.clip(resample(audio, sr), -1, 1) * 32767).astype("<i2")
        silence = np.zeros(8000, dtype="<i2")
        stream = np.concatenate([silence, pcm16, silence, silence, silence]).tobytes()

        async def go():
            ok = await sup.remote.start(host="127.0.0.1", port=ports[0], secure_port=ports[1])
            try:
                if not sup.remote.secure_running:
                    raise RuntimeError(f"secure listener didn't start: {sup.remote.secure_error}")
                code, _ = sup.devices.current_code()
                token = sup.devices.pair(code, "self-test")[1]
                ctx = ssl.create_default_context(cafile=str(sup.remote.certs.ca_pem))   # trusts ONLY JARVIS's authority
                async with websockets.connect(f"wss://localhost:{ports[1]}/remote/ws?token={token}", ssl=ctx,
                                              max_size=8 * 1024 * 1024) as ws:
                    async def until(kind, timeout=60):
                        while True:
                            m = await asyncio.wait_for(ws.recv(), timeout)
                            if isinstance(m, str) and json.loads(m).get("type") == kind:
                                return json.loads(m)
                    hello = await until("hello")
                    if not hello["voice"]["secure"]:
                        raise RuntimeError("the secure link didn't report itself as secure")
                    for i, msg in enumerate([{"type": "stop"}, {"type": "mute", "muted": False},
                                             {"type": "protocol", "name": "work_mode"}], 1):
                        await ws.send(json.dumps({**msg, "id": i}))
                        a = await until("ack", 10)
                        if a["id"] != i or not a["ok"]:
                            raise RuntimeError(f"command {msg['type']} answered {a}")
                    await ws.send(json.dumps({"type": "voice", "mode": "talk", "speak": True, "id": 9}))
                    a = await until("ack", 10)
                    if not a["ok"]:
                        raise RuntimeError(f"voice refused: {a}")
                    for i in range(0, len(stream), 3200):
                        await ws.send(stream[i:i + 3200])
                    heard = await until("heard", 120)
                    if "london" not in heard["text"].lower():
                        raise RuntimeError(f"phone audio transcribed as {heard['text']!r}")
                    if not heard_requests or not callable(heard_requests[0][2]):
                        raise RuntimeError("the request wasn't routed back to the phone")
                    t = np.arange(24000) / 24000
                    heard_requests[0][2]((0.2 * np.sin(2 * np.pi * 330 * t)).astype("float32"), 24000, "Reply.")
                    meta = await until("audio", 10)
                    wav = await asyncio.wait_for(ws.recv(), 10)
                    if not (isinstance(wav, bytes) and wav[:4] == b"RIFF" and len(wav) == meta["bytes"]):
                        raise RuntimeError("his voice didn't arrive on the phone as audio")
                    await ws.send(json.dumps({"type": "voice", "mode": "off"}))
                prof = sup.remote.certs.mobileconfig()
                return ok, heard["text"], len(wav), len(prof)
            finally:
                await sup.remote.stop()
        ok, text, nwav, nprof = asyncio.run(go())
        return f"wss with JARVIS's own certificate · 4 acks · phone audio heard as {text!r} · {nwav} B reply · profile {nprof} B"

    @check("ears: Voice ID knows one voice from others (Kokoro voices as stand-ins)", required=False, timeout=240)
    def _():
        import tempfile
        from pathlib import Path as P

        from jarvis.voice.speaker import VoiceID
        from jarvis.voice.tts import KokoroONNX

        lines = ["Hey Jarvis, what's the weather like today?", "Hey Jarvis, what's on my calendar this afternoon?",
                 "Hey Jarvis, play some music in the living room.", "Hey Jarvis, remind me to call my mother.",
                 "Hey Jarvis, how long will it take me to get to work?"]

        def say(voice, text):
            k = KokoroONNX(voice, 1.0, state["cfg"].models_dir)
            return resample(k.synth(text), k.sample_rate)
        with tempfile.TemporaryDirectory() as tmp:
            vid = VoiceID(state["cfg"].models_dir, P(tmp) / "vp.json")
            if not vid.load():
                raise RuntimeError("Voice ID model didn't load")
            vid.profile.enrol([vid.model.embed(say("bm_george", t)) for t in lines[:3]])
            res = {v: vid.check(say(v, lines[4]), learn=False) for v in ("bm_george", "bf_emma", "am_adam")}
        if res["bm_george"][0] != "you" or res["bf_emma"][0] == "you" or res["am_adam"][0] == "you":
            raise RuntimeError(f"verdicts {res}")
        return " · ".join(f"{v}: {k} {s:.2f}" for v, (k, s) in res.items())

    @check("ears: echo cancellation buffers (Apple voice processing)", timeout=60)
    def _():
        import AVFoundation as AV
        import numpy as np

        from jarvis.voice.apple_io import PLAY_RATE, StreamResampler, channel, make_buffer

        fmt = AV.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(PLAY_RATE, 1)
        x = (0.3 * np.sin(np.arange(2400) / 10)).astype(np.float32)
        buf = make_buffer(fmt, x)
        back = np.array(channel(buf, int(buf.frameLength())), copy=True)
        if buf.frameLength() != 2400 or not np.allclose(back, x):
            raise RuntimeError(f"PCM buffer round trip failed ({buf.frameLength()} frames)")
        out = StreamResampler(48000).process(np.zeros(4800, np.float32))
        return f"PCM buffer write/read ok · resampler 48k→16k gives {len(out)} samples per 0.1 s"

    @check("ears: echo cancellation engine starts (needs a microphone; none on CI)", required=False, timeout=60)
    def _():
        from jarvis.voice.apple_io import VoiceIO

        vio = VoiceIO()
        try:
            if not vio.start():
                raise RuntimeError(vio.error or "didn't start")
            import time as _t
            _t.sleep(1.5)
            return f"voice processing on at {vio.in_rate:.0f} Hz · frames flowing: {vio.frames_flowing(2.0)}"
        finally:
            vio.stop()

    @check("email: reads Mailspring's local mail and ranks what needs you", timeout=60)
    def _():
        import sqlite3
        import tempfile
        from pathlib import Path as P

        from jarvis.mac import mailspring as ms
        with tempfile.TemporaryDirectory() as tmp:
            db = ms.make_sample(P(tmp) / "Mailspring")
            writer = sqlite3.connect(str(db))
            writer.execute("BEGIN IMMEDIATE")      # as if Mailspring were syncing right now
            try:
                got = ms.unread(days=2, path=db)
            finally:
                writer.rollback()
                writer.close()
            if isinstance(got, str) or [g["id"] for g in got] != ["m1", "m2"]:
                raise RuntimeError(f"got {got}")
            body = ms.read("m1", path=db)
            if "signed by 5pm" not in body["body"]:
                raise RuntimeError(f"body {body}")
        real = ms.db_path()
        return (f"sample inbox ranked {[g['id'] for g in got]} (IMAP folder + Gmail label, read/archived/draft "
                f"left out) · read() ok · this Mac: {'mail found' if real else 'no Mailspring mail yet'} · "
                f"Mailspring {'installed' if ms.installed() else 'not installed'}")

    @check("hands: JARVIS types into TextEdit by himself (find → click → type → read back)", required=False,
           timeout=120)
    def _():
        import subprocess
        import tempfile
        import time as _t
        from pathlib import Path as P

        import ApplicationServices as AX
        from AppKit import NSRunningApplication

        from jarvis.mac import control
        from jarvis.mac import screen_elements as se

        doc = P(tempfile.mkdtemp()) / "jarvis-hands.txt"
        doc.write_text("")
        subprocess.run(["open", "-a", "TextEdit", str(doc)], capture_output=True, timeout=20)
        try:
            pid, area = None, None
            for _ in range(40):
                apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit")
                if apps:
                    pid = int(apps[0].processIdentifier())
                    area = next((e for e in se.ax_elements(pid, "TextEdit") if e.role == "AXTextArea"), None)
                    if area is not None:
                        break
                _t.sleep(0.5)
            if area is None:
                raise RuntimeError("couldn't find TextEdit's text area through Accessibility")
            control.bring_to_front(pid, "TextEdit")
            control.click(area.x + area.w / 2, area.y + min(area.h / 2, 40))
            _t.sleep(0.3)
            control.type_text("JARVIS was here")
            control.press("return")
            _t.sleep(0.8)
            app = AX.AXUIElementCreateApplication(pid)
            err, el = AX.AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
            err2, val = AX.AXUIElementCopyAttributeValue(el, "AXValue", None) if not err and el is not None else (1, None)
            got = str(val or "")
            if "JARVIS was here" not in got:
                raise RuntimeError(f"typed, but the document reads {got[:60]!r}")
            return f"found the text area at ({round(area.x)}, {round(area.y)}), clicked, typed and read back {got.strip()!r}"
        finally:
            subprocess.run(["pkill", "-x", "TextEdit"], capture_output=True)

    @check("WhatsApp: reads the Mac app's chats (sample) and reports access on this Mac", timeout=60)
    def _():
        import os
        import tempfile
        from pathlib import Path as P

        from jarvis.mac import whatsapp as wa
        with tempfile.TemporaryDirectory() as tmp:
            db = wa.make_sample(P(tmp) / "wa")
            old = os.environ.get("JARVIS_WHATSAPP_DIR")
            os.environ["JARVIS_WHATSAPP_DIR"] = str(db.parent)
            try:
                got = wa.unread()
                chat = wa.recent("Pepper")
            finally:
                if old is None:
                    os.environ.pop("JARVIS_WHATSAPP_DIR", None)
                else:
                    os.environ["JARVIS_WHATSAPP_DIR"] = old
            if [c["chat"] for c in got][:1] != ["Pepper Potts"] or not chat or len(chat["messages"]) < 3:
                raise RuntimeError(f"got {got} / {chat}")
        return (f"sample: {len(got)} unread chats, people first · this Mac: chats {wa.can_read()} · "
                f"WhatsApp {'installed' if wa.installed() else 'not installed'}")

    @check("setup + HUD server")
    def _():
        from fastapi.testclient import TestClient

        from jarvis.app.supervisor import Supervisor
        from jarvis.hud.server import create_app
        sup = Supervisor(state["cfg"])
        client = TestClient(create_app(supervisor=sup))
        out = {}
        for path in ("/setup", "/", "/api/setup/state", "/static/onboarding.js", "/static/reactor.js",
                     "/mini", "/static/mini.js", "/static/mini.css", "/guide", "/static/guide.js",
                     "/static/guide.css"):
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

    @check("monthly-plan brain drives the real Claude Code CLI (mock API, no account)", required=False, timeout=180)
    def _():
        claude = os.environ.get("JARVIS_SELFTEST_CLAUDE_BIN")
        if not claude:
            return "skipped (set JARVIS_SELFTEST_CLAUDE_BIN)"
        return _claude_code_roundtrip(state["cfg"], claude)

    failed = [r for r in RESULTS if not r["ok"] and r["required"]]
    # every top-level package the checks loaded: what the app really uses (for keeping the bundle lean)
    loaded = sorted({m.split(".")[0] for m in list(sys.modules)})
    report = {"ok": not failed, "results": RESULTS, "python": sys.version, "executable": sys.executable,
              "modules": loaded}
    out = os.environ.get("JARVIS_SELFTEST_OUT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, default=str))
    log("SELFTEST PASSED" if not failed else f"SELFTEST FAILED: {[r['check'] for r in failed]}")
    os._exit(0 if not failed else 1)  # don't wait on any stuck worker thread


def _claude_code_roundtrip(cfg, claude_bin: str) -> str:
    """Spin a tiny mock of the Messages API, point the real CLI at it with a fake key (external
    traffic black-holed), and check a JARVIS tool gets called through the MCP endpoint."""
    import asyncio
    import json as _json
    import socket
    import threading

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, StreamingResponse

    seen = []
    api = FastAPI()

    def sse(events):
        return "".join(f"event: {e['type']}\ndata: {_json.dumps(e)}\n\n" for e in events)

    async def messages(request):
        body = await request.json()
        seen.append(body)
        if not body.get("stream"):
            return JSONResponse({"id": "m", "type": "message", "role": "assistant", "model": "x",
                                 "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                                 "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
        tools = [t.get("name") for t in body.get("tools", [])]
        # done once our tool's result is anywhere in the conversation (Claude Code can add a note after it,
        # e.g. "<total_tokens>…" on Haiku, so it isn't always the last block)
        done = any(isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
                   for m in body["messages"] if m.get("role") == "user")
        if "mcp__jarvis__selftest_ping" in tools and not done:
            blocks = [{"type": "tool_use", "id": f"toolu_{len(seen)}", "name": "mcp__jarvis__selftest_ping", "input": {}}]
            stop = "tool_use"
        else:
            blocks = [{"type": "text", "text": "All systems online, sir."}]
            stop = "end_turn"
        ev = [{"type": "message_start", "message": {"id": f"m{len(seen)}", "type": "message", "role": "assistant",
               "model": "x", "content": [], "stop_reason": None, "stop_sequence": None,
               "usage": {"input_tokens": 1, "output_tokens": 1}}}]
        for i, b in enumerate(blocks):
            if b["type"] == "text":
                ev += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                       {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}}]
            else:
                ev += [{"type": "content_block_start", "index": i, "content_block": {**b, "input": {}}},
                       {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": "{}"}}]
            ev.append({"type": "content_block_stop", "index": i})
        ev += [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 3}},
               {"type": "message_stop"}]
        return StreamingResponse(iter([sse(ev)]), media_type="text/event-stream")

    api.add_route("/v1/messages", messages, methods=["POST"])

    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)

    from jarvis.core.cli_brains import ClaudeCodeBrain
    from jarvis.tools.context import ToolContext
    from jarvis.tools.registry import ToolRegistry

    reg = ToolRegistry()
    pinged = []

    @reg.tool("Self-test ping")
    def selftest_ping(ctx):
        pinged.append(True)
        return "pong"

    home = Path(tempfile.mkdtemp(prefix="cchome-"))
    cfg.data["brain"]["claude_path"] = claude_bin
    cfg.data["brain"]["cli_env"] = {
        "ANTHROPIC_API_KEY": "sk-ant-selftest-not-real", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
        "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "HOME": str(home)}
    brain = ClaudeCodeBrain(cfg, reg, None, ToolContext(cfg=cfg))

    async def go():
        try:
            return await brain.respond("ping", on_sentence=None)
        finally:
            await brain._stop()
            if brain.mcp:
                await brain.mcp.stop()

    reply = asyncio.run(go())
    srv.should_exit = True
    if not pinged:
        raise RuntimeError(f"tool not called (requests: {len(seen)}, reply {reply!r})")
    return f"tool called via MCP; reply {reply!r}"
