"""Enrolled face data: saved atomically and privately; a damaged file doesn't switch recognition off."""
import stat

import numpy as np

from jarvis.vision.faces import FaceEngine


def test_face_data_is_private_and_survives_damage(tmp_path):
    fe = FaceEngine(tmp_path / "models", tmp_path / "faces")
    (tmp_path / "faces").mkdir()
    fe.known = {"Tony": np.ones((2, 128), np.float32)}
    fe._save_known()
    p = tmp_path / "faces" / "embeddings.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    again = FaceEngine(tmp_path / "models", tmp_path / "faces")
    again._load_known()
    assert again.known["Tony"].shape == (2, 128)
    p.write_text("{not json")
    broken = FaceEngine(tmp_path / "models", tmp_path / "faces")
    broken._load_known()
    assert broken.known == {}
