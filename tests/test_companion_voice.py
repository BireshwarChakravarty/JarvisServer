"""Talking through the iPhone: the private certificate, the phone's voice loop, and his voice
being sent to the phone instead of the Mac's speakers."""
import asyncio
import plistlib
import shutil
import ssl
import socket
import subprocess
import threading
import time

import numpy as np
import pytest

from jarvis.remote import voice as rvoice
from jarvis.remote.tls import Certificates

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")


# ── certificates ───────────────────────────────────────────────────────
@needs_openssl
def test_certificate_a_phone_can_trust(tmp_path):
    c = Certificates(tmp_path / "tls", "Test Mac")
    pem, key = c.ensure_server(["test-mac.local"], ["192.168.1.5", "not-an-ip"])
    out = subprocess.run(["openssl", "verify", "-CAfile", str(c.ca_pem), str(pem)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    text = subprocess.run(["openssl", "x509", "-in", str(pem), "-noout", "-text"], capture_output=True, text=True).stdout
    assert "DNS:test-mac.local" in text and "IP Address:192.168.1.5" in text and "TLS Web Server Authentication" in text
    assert "CA:FALSE" in text
    assert oct(key.stat().st_mode)[-3:] == "600" and oct(c.ca_key.stat().st_mode)[-3:] == "600"

    # a client that trusts only the JARVIS authority completes a real TLS handshake by name
    srv = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv.load_cert_chain(pem, key)
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen()

    def serve():
        s, _ = ls.accept()
        with srv.wrap_socket(s, server_side=True) as t:
            t.sendall(b"ok")

    threading.Thread(target=serve, daemon=True).start()
    cli = ssl.create_default_context(cafile=str(c.ca_pem))
    with socket.create_connection(ls.getsockname()) as s, cli.wrap_socket(s, server_hostname="test-mac.local") as t:
        assert t.recv(2) == b"ok"
    ls.close()


@needs_openssl
def test_certificate_is_reused_and_renewed_only_when_needed(tmp_path):
    c = Certificates(tmp_path / "tls", "Test Mac")
    c.ensure_server(["a.local"], ["10.0.0.2"])
    first = c.pem.read_bytes()
    ca = c.ca_pem.read_bytes()
    c.ensure_server(["a.local"], ["10.0.0.2"])
    assert c.pem.read_bytes() == first                       # same names: kept
    c.ensure_server(["a.local"], ["10.0.0.9"])                # new IP address: reissued...
    assert c.pem.read_bytes() != first
    assert c.ca_pem.read_bytes() == ca                        # ...by the same authority: phones keep trusting it


@needs_openssl
def test_the_ios_profile_carries_only_the_authority(tmp_path):
    c = Certificates(tmp_path / "tls", "Test Mac")
    p = plistlib.loads(c.mobileconfig())
    assert p["PayloadType"] == "Configuration" and len(p["PayloadContent"]) == 1
    inner = p["PayloadContent"][0]
    assert inner["PayloadType"] == "com.apple.security.root" and inner["PayloadContent"] == c.ca_der()
    assert b"PRIVATE KEY" not in c.mobileconfig()
    assert plistlib.loads(c.mobileconfig())["PayloadUUID"] == p["PayloadUUID"]   # stable: re-installing replaces it


# ── the phone's voice loop ─────────────────────────────────────────────
class FakeSTT:
    def __init__(self, text):
        self.text, self.calls = text, []

    def transcribe(self, audio):
        self.calls.append(len(audio))
        return self.text


class FakeJarvis:
    def __init__(self, text="What time is it?"):
        self.stt = FakeSTT(text)
        self.requests = []
        self.claims = []

    def queue_request(self, text, source="protocol", route=None, done=None):
        self.requests.append((text, source, route))
        if done is not None:
            done.set_result(True)

    def claim_voice(self, who, hold=4.0):
        self.claims.append(who)
        return True


def speech(seconds, loud=True):
    n = int(16000 * seconds)
    t = np.arange(n) / 16000
    sig = (0.3 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > -0.5)) if loud else np.zeros(n)
    return (sig * 32767).astype("<i2").tobytes()


@pytest.fixture
def energy_vad(monkeypatch):
    # the real VAD (webrtcvad) is trained on voices; a synthetic tone needs a plain energy check
    monkeypatch.setattr(rvoice.VAD, "is_speech",
                        lambda self, f: float(np.sqrt(np.mean((f.astype(np.float32) / 32768) ** 2))) > 0.02)
    monkeypatch.setattr(rvoice, "FOLLOW_UP_SECONDS", 0.6)
    monkeypatch.setattr(rvoice, "FIRST_LISTEN_SECONDS", 1.5)


async def _feed(v, pcm, chunk=3200):
    for i in range(0, len(pcm), chunk):
        v.feed(pcm[i:i + chunk])
        await asyncio.sleep(0.005)


def test_tap_to_talk_hears_you_and_answers_on_the_phone(energy_vad):
    async def run():
        j = FakeJarvis()
        sent = []
        v = rvoice.PhoneVoice(j, "dev1", sent.append, route_factory=lambda: "PHONE", wake_factory=lambda: None)
        assert v.set_mode("talk", speak_here=True) is None
        await asyncio.sleep(0.05)
        await _feed(v, speech(0.2, loud=False) + speech(1.0) + speech(1.2, loud=False))
        for _ in range(100):
            if any(m.get("phase") == "off" for m in sent):
                break
            await asyncio.sleep(0.05)
        return j, sent

    j, sent = asyncio.run(run())
    assert j.requests == [("What time is it?", "phone", "PHONE")]          # answered on this phone
    phases = [m["phase"] for m in sent if m["type"] == "voice"]
    assert phases[:3] == ["listening", "transcribing", "thinking"] and phases[-1] == "off"
    assert any(m == {"type": "heard", "text": "What time is it?"} for m in sent)
    assert j.stt.calls and j.stt.calls[0] >= 16000 * 0.8                   # the whole utterance, not a fragment


def test_silence_ends_quietly_and_says_so(energy_vad):
    async def run():
        j = FakeJarvis()
        sent = []
        v = rvoice.PhoneVoice(j, "dev1", sent.append, route_factory=lambda: None, wake_factory=lambda: None)
        v.set_mode("talk")
        await _feed(v, speech(2.0, loud=False))
        for _ in range(80):                                       # the listen window is real time
            if sent and sent[-1].get("phase") == "off":
                break
            await asyncio.sleep(0.05)
        return j, sent

    j, sent = asyncio.run(run())
    assert j.requests == [] and sent[-1] == {"type": "voice", "phase": "off", "heard": False}


def test_answer_on_the_mac_when_asked(energy_vad):
    async def run():
        j = FakeJarvis()
        v = rvoice.PhoneVoice(j, "d", lambda m: None, route_factory=lambda: "PHONE", wake_factory=lambda: None)
        v.set_mode("talk", speak_here=False)
        await _feed(v, speech(1.0) + speech(1.0, loud=False))
        await asyncio.sleep(0.2)
        v.stop()
        return j

    assert asyncio.run(run()).requests[0][2] is None


def test_hey_jarvis_on_the_phone_starts_a_conversation(energy_vad):
    class Wake:
        def __init__(self):
            self.n = 0

        def reset(self):
            pass

        def process(self, frame):
            self.n += 1
            return self.n == 3

    async def run():
        j = FakeJarvis()
        sent = []
        v = rvoice.PhoneVoice(j, "dev9", sent.append, route_factory=lambda: "PHONE", wake_factory=Wake)
        v.set_mode("wake")
        await asyncio.sleep(0.05)
        await _feed(v, speech(0.3, loud=False))                  # wake fires on the 3rd frame
        await asyncio.sleep(0.1)
        await _feed(v, speech(1.0) + speech(1.0, loud=False))
        for _ in range(60):
            if j.requests:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        mode = v.mode
        v.stop()
        return j, sent, mode

    j, sent, mode = asyncio.run(run())
    assert {"type": "wake"} in sent and j.claims == ["phone:dev9"]
    assert j.requests and j.requests[0][0] == "What time is it?"
    assert mode == "wake"                                         # and goes back to waiting for "Hey JARVIS"


def test_voice_refuses_without_speech_recognition():
    j = FakeJarvis()
    j.stt = None
    v = rvoice.PhoneVoice(j, "d", lambda m: None, route_factory=lambda: None, wake_factory=lambda: None)
    assert "Speech recognition" in v.set_mode("talk")
    assert v.mode == "off"


def test_audio_is_ignored_while_he_answers():
    v = rvoice.PhoneVoice(FakeJarvis(), "d", lambda m: None, route_factory=lambda: None, wake_factory=lambda: None)
    v.mode = "talk"
    v._busy = True
    v.feed(speech(1.0))
    assert v.q.empty()
    v._busy = False
    v.feed(speech(0.5) + b"\x01")                                 # odd byte count is handled
    assert v.q.qsize() == int(16000 * 0.5) // 1280


# ── the Mac's speaker hands his voice to the phone ─────────────────────
def test_speaker_route_sends_instead_of_playing(monkeypatch):
    from jarvis.voice import tts

    class Backend:
        name, sample_rate = "fake", 24000

        def synth(self, text):
            return np.full(2400, 0.1, dtype=np.float32)          # 0.1 s

    sp = tts.Speaker(Backend())
    sp._sd = None
    played, sent = [], []
    monkeypatch.setattr(sp, "_play", lambda *a, **k: played.append(a))
    sp.start()
    sp.route = lambda audio, sr, text: sent.append((len(audio), sr, text)) or True
    sp.enqueue("Hello there.")
    assert sp._idle.wait(5)
    assert sent and sent[0][1] == 24000 and sent[0][2] == "Hello there." and not played
    sp.route = lambda audio, sr, text: False                     # phone gone: fall back to the Mac
    sp.enqueue("Again.")
    assert sp._idle.wait(5)
    assert played
    sp.route = None
    sp.close()


def test_mac_and_phone_dont_both_answer(cfg):
    from jarvis.core.orchestrator import Jarvis

    j = Jarvis.__new__(Jarvis)
    assert j.claim_voice("phone:a") is True
    assert j.claim_voice("mac") is False                            # the phone heard it first
    assert j.claim_voice("phone:a") is True
    j._voice_claim = ("phone:a", time.monotonic() - 1)
    assert j.claim_voice("mac") is True
