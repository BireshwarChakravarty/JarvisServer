"""Model assets JARVIS downloads on first run, with progress, resume and integrity checks.

Everything lives in <data dir>/models so the signed app bundle is never modified.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.events import bus

log = logging.getLogger("jarvis.models")

HF = "https://huggingface.co"
OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


@dataclass
class Asset:
    name: str                       # path relative to the models dir
    urls: list[str]
    size: int = 0                   # expected bytes (approximate is fine; used for progress before HEAD)


@dataclass
class Group:
    key: str
    title: str
    purpose: str
    assets: list[Asset] = field(default_factory=list)
    optional: bool = False          # only fetched (and shown) when the feature that needs it is chosen


# the larger, more accurate recognisers live in other repos and use a JSON vocabulary
WHISPER_REPOS = {
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", {
        "config.json": 2_263, "model.bin": 1_617_884_929, "preprocessor_config.json": 340,
        "tokenizer.json": 2_710_337, "vocabulary.json": 1_068_114}),
    "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", {
        "config.json": 2_690, "model.bin": 1_512_927_867, "preprocessor_config.json": 340,
        "tokenizer.json": 2_480_617, "vocabulary.json": 1_068_114}),
}


def whisper_assets(model: str) -> list[Asset]:
    if model in WHISPER_REPOS:
        repo, files = WHISPER_REPOS[model]
        return [Asset(f"whisper-{model}/{f}", [f"{HF}/{repo}/resolve/main/{f}"], size) for f, size in files.items()]
    repo = f"Systran/faster-whisper-{model}"
    sizes = {"tiny.en": 75_000_000, "base.en": 145_000_000, "small.en": 484_000_000,
             "medium.en": 1_530_000_000, "large-v3": 3_090_000_000}
    out = []
    for fname in ("config.json", "tokenizer.json", "vocabulary.txt", "model.bin"):
        out.append(Asset(f"whisper-{model}/{fname}", [f"{HF}/{repo}/resolve/main/{fname}"],
                         sizes.get(model, 500_000_000) if fname == "model.bin" else 2_000_000))
    return out


def groups(stt_model: str = "small.en") -> list[Group]:
    from jarvis.voice import qwen_tts as qwen

    return [
        Group("wake", "Wake word", "Hears “Hey Jarvis”", [
            Asset("oww/melspectrogram.onnx", [f"{OWW}/melspectrogram.onnx"], 1_087_958),
            Asset("oww/embedding_model.onnx", [f"{OWW}/embedding_model.onnx"], 1_326_578),
            Asset("oww/hey_jarvis_v0.1.onnx", [f"{OWW}/hey_jarvis_v0.1.onnx"], 1_271_370),
        ]),
        Group("voice", "Voice", "JARVIS's British voice (Kokoro)", [
            Asset("kokoro-v1.0.onnx", [f"{KOKORO}/kokoro-v1.0.onnx"], 325_532_387),
            Asset("voices-v1.0.bin", [f"{KOKORO}/voices-v1.0.bin"], 28_214_398),
        ]),
        Group("human", "Natural voice", "His natural, human-sounding voice (Qwen3-TTS, about 1.7 GB)", [
            Asset(f"{qwen.TIERS['small']['dir']}/{f}", [f"{HF}/{qwen.TIERS['small']['repo']}/resolve/main/{f}"], size)
            for f, size in qwen.TIERS["small"]["files"].items()], optional=True),
        Group("ears", "Speech recognition",
              "Understands what you say (Whisper), when you've finished (Smart Turn) and that it's you (Voice ID)",
              whisper_assets(stt_model) + [
                  Asset("turn/smart-turn-v3.2-cpu.onnx",
                        [f"{HF}/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"], 8_679_182),
                  Asset("speaker/campplus_en_voxceleb.onnx",       # Voice ID (3D-Speaker CAM++, Apache-2.0)
                        [f"{HF}/csukuangfj/speaker-embedding-models/resolve/main/"
                         "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"], 29_596_979)]),
        Group("eyes", "Vision", "Recognises your face and gestures", [
            Asset("face_detection_yunet_2023mar.onnx", [
                f"{HF}/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
                "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            ], 232_589),
            Asset("face_recognition_sface_2021dec.onnx", [
                f"{HF}/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
                "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
            ], 38_696_353),
            Asset("gesture_recognizer.task", [
                "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task",
            ], 8_373_440),
        ]),
    ]


class ModelManager:
    def __init__(self, models_dir: Path, stt_model: str = "small.en") -> None:
        self.dir = models_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.groups = groups(stt_model)
        self.stt_model = stt_model
        self.progress: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._running: dict[str, bool] = {}

    def path(self, name: str) -> Path:
        return self.dir / name

    def group(self, key: str) -> Group:
        return next(g for g in self.groups if g.key == key)

    def ready(self, key: str) -> bool:
        return all((self.dir / a.name).exists() for a in self.group(key).assets)

    wanted: set = set()

    def want(self, key: str) -> None:
        """Ask for an optional group (the natural voice, once you choose it)."""
        self.wanted = set(self.wanted) | {key}

    def _shown(self, g: Group) -> bool:
        return not g.optional or g.key in self.wanted or self.ready(g.key)

    def status(self) -> list[dict]:
        out = []
        for g in self.groups:
            if not self._shown(g):
                continue
            total = sum(a.size for a in g.assets)
            p = self.progress.get(g.key, {})
            out.append({
                "key": g.key, "title": g.title, "purpose": g.purpose, "ready": self.ready(g.key),
                "total": p.get("total", total), "done": p.get("done", total if self.ready(g.key) else 0),
                "error": p.get("error"), "running": self._running.get(g.key, False),
            })
        return out

    def prune(self, keep_natural: bool) -> list[str]:
        """Delete downloaded models JARVIS no longer uses, so he takes only the room he needs: the larger
        natural-voice model earlier versions fetched, speech models other than the one in use, and the
        natural voice itself while a Classic voice is chosen (it downloads again if you pick one)."""
        import shutil

        from jarvis.voice import qwen_tts as qwen

        stale = list(qwen.STALE_DIRS)
        stale += [p.name for p in self.dir.glob("whisper-*") if p.name != f"whisper-{self.stt_model}"]
        if not keep_natural and not self._running.get("human"):
            stale += [t["dir"] for t in qwen.TIERS.values()]
        removed = []
        for name in stale:
            p = self.dir / name
            if p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
                removed.append(name)
                log.info("removed %s (%.1f GB): no longer used", name, size / 1e9)
        return removed

    # ── downloading ──────────────────────────────────────────────────────
    def ensure(self, key: str) -> bool:
        """Blocking: download every missing asset of a group. Returns True when ready."""
        with self._lock:
            if self._running.get(key):
                return False
            self._running[key] = True
        g = self.group(key)
        total = sum(a.size for a in g.assets)
        done_before = sum(a.size for a in g.assets if (self.dir / a.name).exists())
        state = {"total": total, "done": done_before, "error": None}
        self.progress[key] = state
        try:
            for a in g.assets:
                dest = self.dir / a.name
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                self._download(a, dest, state, key)
            state["done"] = state["total"]
            bus.emit("download", group=key, done=state["done"], total=state["total"], ready=True)
            return True
        except Exception as exc:
            state["error"] = str(exc)[:200]
            log.error("download of %s failed: %s", key, exc)
            bus.emit("download", group=key, error=state["error"], done=state["done"], total=state["total"])
            return False
        finally:
            self._running[key] = False

    def _download(self, asset: Asset, dest: Path, state: dict, key: str) -> None:
        import httpx

        last_exc: Exception | None = None
        part = dest.with_name(dest.name + ".part")
        for url in asset.urls:
            for attempt in range(3):
                try:
                    have = part.stat().st_size if part.exists() else 0
                    headers = {"Range": f"bytes={have}-"} if have else {}
                    with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                                      timeout=httpx.Timeout(30, read=120)) as r:
                        if r.status_code == 416:  # already complete
                            part.rename(dest)
                            return
                        r.raise_for_status()
                        if have and r.status_code != 206:  # server ignored Range: restart
                            have = 0
                            part.unlink(missing_ok=True)
                        length = int(r.headers.get("content-length", 0)) + have
                        if length:  # correct the estimate with the real size
                            state["total"] += length - asset.size
                            asset.size = length
                        base = state["done"]
                        got = have
                        last_emit = 0.0
                        with open(part, "ab" if have else "wb") as fh:
                            for chunk in r.iter_bytes(1 << 20):
                                fh.write(chunk)
                                got += len(chunk)
                                state["done"] = base + got
                                now = time.monotonic()
                                if now - last_emit > 0.25:
                                    last_emit = now
                                    bus.emit("download", group=key, done=state["done"], total=state["total"])
                    if length and part.stat().st_size < length:
                        raise OSError("incomplete download")
                    part.rename(dest)
                    return
                except Exception as exc:
                    last_exc = exc
                    log.warning("download %s from %s failed (attempt %d): %s", asset.name, url, attempt + 1, exc)
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"couldn't download {asset.name}: {last_exc}")

    async def ensure_all(self, order: list[str] | None = None) -> bool:
        ok = True
        for key in order or [g.key for g in self.groups]:
            ok = await asyncio.to_thread(self.ensure, key) and ok
        return ok
