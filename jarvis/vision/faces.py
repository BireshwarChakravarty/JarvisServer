"""Face detection + recognition with OpenCV's YuNet (detector) and SFace (recogniser).
Both are tiny ONNX models from the OpenCV model zoo — no dlib, no compiling, fast on CPU.

Enrol yourself once with `jarvis enroll` (captures ~15 samples), then JARVIS knows you."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from jarvis.events import bus

log = logging.getLogger("jarvis.faces")

MODELS = {  # OpenCV model zoo — Hugging Face mirror first, GitHub (LFS) as fallback
    "face_detection_yunet_2023mar.onnx": [
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ],
    "face_recognition_sface_2021dec.onnx": [
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ],
}


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h in the processed frame
    score: float
    name: str | None = None
    similarity: float = 0.0
    landmarks: list = field(default_factory=list)


class FaceEngine:
    def __init__(self, models_dir: Path, faces_dir: Path, threshold: float = 0.42,
                 process_every: float = 0.35, owner_name: str = "owner") -> None:
        self.models_dir = models_dir
        self.faces_dir = faces_dir
        self.threshold = threshold
        self.process_every = process_every
        self.owner_name = owner_name
        self.detector = None
        self.recognizer = None
        self.known: dict[str, np.ndarray] = {}  # name -> (N, 128) normalised embeddings
        self.latest: list[Face] = []
        self.frame_size = (0, 0)
        self._last = 0.0
        self._lock = threading.Lock()
        self.available = False

    # ── setup ────────────────────────────────────────────────────────────
    def load(self) -> bool:
        try:
            import cv2

            from jarvis.voice.tts import _download

            for fname, urls in MODELS.items():
                path = self.models_dir / fname
                if not path.exists():
                    log.info("downloading %s …", fname)
                    _download(urls, path)
            self.detector = cv2.FaceDetectorYN.create(
                str(self.models_dir / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.85, 0.3, 5000)
            self.recognizer = cv2.FaceRecognizerSF.create(
                str(self.models_dir / "face_recognition_sface_2021dec.onnx"), "")
            self._load_known()
            self.available = True
            log.info("face engine ready (%d known people)", len(self.known))
        except Exception as exc:
            log.warning("face engine unavailable: %s", exc)
            self.available = False
        return self.available

    def _db_path(self) -> Path:
        return self.faces_dir / "embeddings.json"

    def _load_known(self) -> None:
        path = self._db_path()
        if path.exists():
            data = json.loads(path.read_text())
            self.known = {k: np.asarray(v, dtype=np.float32) for k, v in data.items()}

    def _save_known(self) -> None:
        self._db_path().write_text(json.dumps({k: v.tolist() for k, v in self.known.items()}))

    # ── core ─────────────────────────────────────────────────────────────
    def _small(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        import cv2

        h, w = frame.shape[:2]
        scale = 640 / w if w > 640 else 1.0
        if scale != 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        return frame, scale

    def detect(self, frame: np.ndarray) -> tuple[np.ndarray, list]:
        small, _ = self._small(frame)
        h, w = small.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(small)
        return small, ([] if faces is None else list(faces))

    def embed(self, small: np.ndarray, det) -> np.ndarray:
        aligned = self.recognizer.alignCrop(small, det)
        feat = self.recognizer.feature(aligned).reshape(-1).astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)

    def identify(self, emb: np.ndarray) -> tuple[str | None, float]:
        best, best_sim = None, 0.0
        for name, bank in self.known.items():
            sim = float(np.max(bank @ emb))
            if sim > best_sim:
                best, best_sim = name, sim
        return (best, best_sim) if best_sim >= self.threshold else (None, best_sim)

    def process(self, frame: np.ndarray) -> None:
        """Camera-thread processor: throttled detection + recognition."""
        now = time.monotonic()
        if not self.available or now - self._last < self.process_every:
            return
        self._last = now
        small, dets = self.detect(frame)
        faces: list[Face] = []
        for det in dets[:4]:
            x, y, w, h = (int(v) for v in det[:4])
            face = Face(box=(x, y, w, h), score=float(det[-1]),
                        landmarks=[(float(det[i]), float(det[i + 1])) for i in range(4, 14, 2)] if len(det) >= 15 else [])
            if self.known and w >= 40:
                face.name, face.similarity = self.identify(self.embed(small, det))
            faces.append(face)
        with self._lock:
            self.latest = faces
            self.frame_size = small.shape[1], small.shape[0]
        bus.emit("faces", w=small.shape[1], h=small.shape[0], faces=[
            {"box": f.box, "name": f.name, "sim": round(f.similarity, 2)} for f in faces
        ])
        self._update_attention(faces)

    def _update_attention(self, faces: list[Face]) -> None:
        """Is the person at the Mac looking at the screen? Hysteresis so a glance doesn't flicker."""
        from jarvis.vision.motion import attention

        main = max(faces, key=lambda f: f.box[2] * f.box[3], default=None)
        score = attention(main.landmarks, main.box[2]) if main and len(main.landmarks) >= 3 else 0.0
        self._att = 0.5 * getattr(self, "_att", 0.0) + 0.5 * score
        looking = getattr(self, "looking", False)
        if not looking and self._att > 0.62:
            self.looking = True
        elif looking and self._att < 0.35:
            self.looking = False
        if getattr(self, "looking", False) != looking:
            bus.emit("attention", looking=self.looking)

    def snapshot(self) -> list[Face]:
        with self._lock:
            return list(self.latest)

    # ── enrolment ────────────────────────────────────────────────────────
    def enroll(self, name: str, frames: list[np.ndarray]) -> int:
        embs = []
        for frame in frames:
            small, dets = self.detect(frame)
            if len(dets) != 1:
                continue
            embs.append(self.embed(small, dets[0]))
        if not embs:
            return 0
        bank = np.stack(embs)
        if name in self.known:
            bank = np.concatenate([self.known[name], bank])[-60:]
        self.known[name] = bank
        self._save_known()
        return len(embs)

    def forget(self, name: str) -> bool:
        if name in self.known:
            del self.known[name]
            self._save_known()
            return True
        return False
