"""List microphones and cameras by name (a Mac mini has neither built in, so you pick)."""
from __future__ import annotations

import sys


def microphones() -> list[dict]:
    try:
        import sounddevice as sd

        default = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append({"index": i, "name": d["name"], "default": i == default})
        return out
    except Exception:
        return []


def cameras() -> list[dict]:
    """AVFoundation's device order matches OpenCV's CAP_AVFOUNDATION indices."""
    if sys.platform != "darwin":
        return []
    try:
        import AVFoundation as AV

        devs = AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo) or []
        return [{"index": i, "name": str(d.localizedName())} for i, d in enumerate(devs)]
    except Exception:
        return []
