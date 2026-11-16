"""Real companion server (http 7790 + https 7791) with a stand-in JARVIS, for scripts/companion_e2e.js."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from jarvis.config import load_config
from jarvis.events import bus
from jarvis.remote import voice as rvoice
from jarvis.remote.devices import DeviceStore
from jarvis.remote.server import RemoteServer

home = Path(sys.argv[1]); home.mkdir(parents=True, exist_ok=True)
log = open(home / "events.log", "a", buffering=1)
def note(*a):
    print(*a, file=log, flush=True)

# synthetic test audio isn't a voice: use an energy check instead of webrtcvad
rvoice.VAD.is_speech = lambda self, f: float(np.sqrt(np.mean((f.astype(np.float32) / 32768) ** 2))) > 0.02


class STT:
    def transcribe(self, audio):
        note("STT", len(audio))
        return f"Test request of {len(audio) / 16000:.1f} seconds"


class FakeJarvis:
    def __init__(self):
        self.stt, self.ctx, self.wake = STT(), None, None

    async def hud_command(self, msg):
        note("CMD", json.dumps(msg))
        if msg.get("type") == "mute":
            bus.emit("mic", muted=bool(msg.get("muted")))

    def claim_voice(self, who, hold=4.0):
        return True

    def queue_request(self, text, source="protocol", route=None, done=None):
        note("REQ", text, source, "route" if route else "mac")
        asyncio.get_running_loop().create_task(self._answer(text, route, done))

    async def _answer(self, text, route, done):
        bus.emit("transcript", text=text, source="phone")
        bus.emit("state", state="thinking")
        await asyncio.sleep(0.4)
        reply = "Certainly. This is a test reply played on your phone."
        t = np.arange(24000) / 24000
        audio = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
        bus.emit("state", state="speaking")
        bus.emit("speaking", text=reply, duration=1.0)
        if route:
            note("ROUTED", route(audio, 24000, reply))
        await asyncio.sleep(1.1)
        bus.emit("state", state="idle")
        if done is not None and not done.done():
            done.set_result(True)


cfg = load_config()
cfg.home = home
store = DeviceStore(home / "companion.json")
sup = SimpleNamespace(cfg=cfg, jarvis=FakeJarvis(), show_window=lambda url=None: note("CMD show"))
sup.remote = RemoteServer(sup, store)


async def main():
    bus.bind(asyncio.get_running_loop())
    ok = await sup.remote.start(host="127.0.0.1", port=7790, secure_port=7791)
    note("STARTED", ok, sup.remote.secure_running, sup.remote.secure_error)
    bus.emit("protocols", items=[{"name": "work_mode"}, {"name": "movie_night"}])
    while True:
        (home / "code").write_text(store.current_code()[0])
        await asyncio.sleep(20)

asyncio.run(main())
