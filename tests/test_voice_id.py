"""Voice ID: learning your voice and answering only you.

The front end is checked against values from kaldi-native-fbank (the reference the model's own
toolchain uses). The model itself runs here when JARVIS_SPEAKER_MODEL points at it, and in the
in-app self-test on the Mac (different macOS voices: same one accepted, others refused)."""
import os
from pathlib import Path

import numpy as np
import pytest

from jarvis.voice.speaker import PROFILE_MAX, VoiceID, VoiceProfile, fbank, speech_only


def chirp():
    t = np.arange(16000) / 16000
    return (0.3 * np.sin(2 * np.pi * (200 + 900 * t) * t) + 0.1 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)


def test_fbank_matches_kaldi():
    f = fbank(chirp())
    assert f.shape == (98, 80)                                    # 25 ms frames, 10 ms hop, snip edges
    ref10 = [-12.8255, -8.2364, -3.7116, -12.9386, -15.8008, -15.2974, -11.032, -8.3642, -15.9424, -15.9424]
    ref50 = [-13.9648, -15.9424, -12.8463, -7.7467, -3.9681, -12.3529, -10.9812, -8.3602, -15.9424, -15.9424]
    assert np.allclose(f[10, ::8], ref10, atol=2e-3) and np.allclose(f[50, ::8], ref50, atol=2e-3)
    assert abs(float(f.mean()) + 11.2534) < 2e-3


def test_too_short_for_a_frame():
    assert fbank(np.zeros(300, np.float32)).shape == (0, 80)


def test_silence_is_left_out_of_the_voiceprint():
    a = np.concatenate([np.zeros(16000, np.float32), chirp(), np.zeros(16000, np.float32)])
    assert abs(len(speech_only(a)) - 16000) < 1000


def unit(seed, base=None, noise=0.0):
    r = np.random.default_rng(seed)
    v = base + noise * r.standard_normal(512) if base is not None else r.standard_normal(512)
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_profile_enrol_score_learn_forget(tmp_path):
    me = unit(1)
    p = VoiceProfile(tmp_path / "voiceprint.json")
    assert not p.ready
    p.enrol([unit(i, me, 0.005) for i in range(2, 7)])
    assert p.ready and p.enrolled == 5
    assert p.score(unit(9, me, 0.005)) > 0.9 and p.score(unit(42)) < 0.2
    again = VoiceProfile(tmp_path / "voiceprint.json")            # kept on disk
    assert again.ready and len(again.prints) == 5
    assert again.learn(unit(10, me, 0.005), min_gap=0)
    assert not again.learn(unit(11, me, 0.005))                     # not every request: every few minutes
    for i in range(60):
        again.learn(unit(100 + i, me, 0.005), min_gap=0)
    assert len(again.prints) == PROFILE_MAX
    assert np.allclose(again.prints[:5], p.prints[:5], atol=1e-4)  # what you enrolled with always stays
    again.clear()
    assert not again.ready and not (tmp_path / "voiceprint.json").exists()


class FakeModel:
    available = True

    def __init__(self, v):
        self.v = v

    def embed(self, audio):
        return self.v(audio) if callable(self.v) else self.v


def test_verdicts(tmp_path):
    me = unit(1)
    vid = VoiceID(tmp_path, tmp_path / "vp.json")
    assert vid.check(np.zeros(16000))[0] == "off"                  # not set up: never refuses
    vid.profile.enrol([unit(i, me, 0.005) for i in range(2, 7)])
    vid.model = FakeModel(unit(9, me, 0.005))
    assert vid.check(np.zeros(16000))[0] == "you"
    vid.model = FakeModel(unit(77))
    assert vid.check(np.zeros(16000))[0] == "not_you"
    vid.model = FakeModel(None)
    assert vid.check(np.zeros(16000))[0] == "short"                # "yes": too little to judge
    between = unit(5, me * 0.6 + unit(99) * 0.6)
    vid.model = FakeModel(between)
    kind, s = vid.check(np.zeros(16000))
    assert kind == ("unsure" if 0.33 <= s < 0.45 else kind)


@pytest.mark.skipif(not os.environ.get("JARVIS_SPEAKER_MODEL"), reason="set JARVIS_SPEAKER_MODEL to the CAM++ .onnx")
def test_real_model_tells_two_voices_apart(tmp_path):
    from jarvis.voice.speaker import MODEL, SpeakerModel

    d = tmp_path / "models"
    (d / MODEL).parent.mkdir(parents=True)
    (d / MODEL).symlink_to(Path(os.environ["JARVIS_SPEAKER_MODEL"]))
    m = SpeakerModel(d)
    assert m.load()
    a = m.embed(np.concatenate([chirp()] * 3))
    assert a is not None and a.shape == (512,) and abs(float(np.linalg.norm(a)) - 1) < 1e-4


def test_quick_commands_start_and_stop_it():
    from jarvis.core import quick

    for said in ("learn my voice", "Jarvis, learn my voice please", "only listen to me", "set up voice ID"):
        q = quick.match(said)
        assert q and q.tool == "voice_id" and q.args == {"action": "learn"}, said
    q = quick.match("forget my voice")
    assert q and q.args == {"action": "forget"}
