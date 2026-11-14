"""HTTP API behind the graphical onboarding (and Settings). Everything a person would
otherwise do in Terminal or System Settings happens here, behind a button."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from jarvis.events import bus

log = logging.getLogger("jarvis.setup")


def _app_bundle() -> Path | None:
    exe = sys.executable or ""
    if ".app/Contents/" in exe:
        return Path(exe.split(".app/Contents/")[0] + ".app")
    return None


def _read_user_config(cfg) -> dict:
    p = cfg.user_config_path
    if p.exists():
        try:
            return yaml.safe_load(p.read_text()) or {}
        except Exception:
            return {}
    return {}


def _write_user_config(cfg, patch: dict) -> dict:
    from jarvis.config import deep_merge

    data = deep_merge(_read_user_config(cfg), patch)
    cfg.user_config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    # keep the live config in step
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cfg.data.get(k), dict):
            cfg.data[k] = deep_merge(cfg.data[k], v)
        else:
            cfg.data[k] = v
    return data


def _has_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        from jarvis.mac.keychain import get_api_key

        return bool(get_api_key())
    except Exception:
        return False


def make_router(sup) -> APIRouter:
    r = APIRouter()

    # ── overall state ──────────────────────────────────────────────────────
    @r.get("/api/setup/state")
    async def state():
        from jarvis.mac import login_item, permissions

        cfg = sup.cfg
        user = _read_user_config(cfg)
        bundle = _app_bundle()
        perms = await asyncio.to_thread(permissions.all_status)
        enrolled = False
        emb = cfg.faces_dir / "embeddings.json"
        if emb.exists():
            try:
                enrolled = cfg.get("owner.name", "") in json.loads(emb.read_text())
            except Exception:
                pass
        return {
            "complete": cfg.setup_complete,
            "running": sup.jarvis is not None,
            "profile": {
                "name": (user.get("owner") or {}).get("name", ""),
                "title": (user.get("owner") or {}).get("title", "sir"),
                "city": (user.get("location") or {}).get("city", ""),
                "units": (user.get("location") or {}).get("units", "metric"),
            },
            "has_key": _has_key(),
            "permissions": perms,
            "models": sup.models.status(),
            "face_enrolled": enrolled,
            "login_item": await asyncio.to_thread(login_item.status),
            "app_path": str(bundle) if bundle else None,
            "in_applications": bool(bundle and str(bundle).startswith(("/Applications/", str(Path.home() / "Applications")))),
            "options": {
                "sentry_mode": bool(cfg.get("vision.sentry_mode", False)),
                "auto_lock_when_away": bool(cfg.get("vision.auto_lock_when_away", False)),
                "voice": cfg.get("voice.tts.voice", "bm_george"),
            },
        }

    # ── profile ────────────────────────────────────────────────────────────
    @r.post("/api/setup/profile")
    async def profile(body: dict = Body(...)):
        name = (body.get("name") or "").strip()[:40]
        if not name:
            return JSONResponse({"ok": False, "error": "Please tell me your name."}, status_code=400)
        patch = {"owner": {"name": name, "title": (body.get("title") or "sir").strip()[:30]},
                 "location": {"city": (body.get("city") or "").strip()[:80] or "London",
                              "units": body.get("units") if body.get("units") in ("metric", "imperial") else "metric"}}
        if body.get("latitude") is not None and body.get("longitude") is not None:
            patch["location"]["latitude"] = float(body["latitude"])
            patch["location"]["longitude"] = float(body["longitude"])
        _write_user_config(sup.cfg, patch)
        return {"ok": True}

    @r.get("/api/setup/geocode")
    async def geocode(q: str = ""):
        import httpx

        if len(q.strip()) < 2:
            return []
        try:
            async with httpx.AsyncClient(timeout=6) as http:
                res = await http.get("https://geocoding-api.open-meteo.com/v1/search",
                                     params={"name": q, "count": 6, "language": "en", "format": "json"})
                out = []
                for it in (res.json().get("results") or []):
                    out.append({"name": it.get("name"), "region": it.get("admin1") or "", "country": it.get("country") or "",
                                "country_code": it.get("country_code") or "",
                                "latitude": it.get("latitude"), "longitude": it.get("longitude")})
                return out
        except Exception:
            return []

    # ── the brain: API key ────────────────────────────────────────────────
    @r.post("/api/setup/key")
    async def key(body: dict = Body(...)):
        k = (body.get("key") or "").strip()
        if not k.startswith("sk-"):
            return JSONResponse({"ok": False, "error": "That doesn't look like an Anthropic API key (they start with sk-)."},
                                status_code=400)

        def validate() -> tuple[bool, str]:
            import anthropic

            try:
                client = anthropic.Anthropic(api_key=k, max_retries=1, timeout=30)
                msg = client.messages.create(model=sup.cfg.get("brain.model", "claude-opus-5-5"), max_tokens=16,
                                             messages=[{"role": "user", "content": "Reply with exactly: online"}])
                return True, getattr(msg, "model", "")
            except anthropic.AuthenticationError:
                return False, "Anthropic rejected that key. Double-check you copied all of it."
            except anthropic.PermissionDeniedError:
                return False, "That key doesn't have access to this model."
            except anthropic.RateLimitError:
                return True, "rate-limited, but the key works"
            except anthropic.APIConnectionError:
                return False, "I couldn't reach Anthropic. Check your internet connection."
            except anthropic.APIStatusError as exc:
                if exc.status_code == 402 or "credit" in str(exc).lower():
                    return False, "The key works, but the account has no credit. Add some at console.anthropic.com › Billing."
                return False, f"Anthropic returned an error ({exc.status_code})."

        ok, info = await asyncio.to_thread(validate)
        if not ok:
            return JSONResponse({"ok": False, "error": info}, status_code=400)
        try:
            from jarvis.mac.keychain import set_api_key

            await asyncio.to_thread(set_api_key, k)
            where = "Keychain"
        except Exception:
            env = sup.cfg.home / ".env"
            env.write_text(f"ANTHROPIC_API_KEY={k}\n")
            env.chmod(0o600)
            where = "file"
        os.environ["ANTHROPIC_API_KEY"] = k
        return {"ok": True, "model": info, "stored_in": where}

    @r.post("/api/setup/open-url")
    async def open_url(body: dict = Body(...)):
        url = body.get("url", "")
        if url.startswith(("https://console.anthropic.com", "https://platform.claude.com", "x-apple.systempreferences:")):
            subprocess.run(["/usr/bin/open", url], capture_output=True)
        return {"ok": True}

    # ── permissions ───────────────────────────────────────────────────────
    @r.get("/api/setup/permissions")
    async def perms():
        from jarvis.mac import permissions

        return await asyncio.to_thread(permissions.all_status)

    @r.post("/api/setup/permissions/{key}")
    async def perm_request(key: str):
        from jarvis.mac import permissions

        if key not in permissions.SETTINGS_PANES:
            return JSONResponse({"ok": False}, status_code=404)
        st = await asyncio.to_thread(permissions.request, key)
        return {"key": key, "status": st}

    @r.post("/api/setup/permissions/{key}/settings")
    async def perm_settings(key: str):
        from jarvis.mac import permissions

        permissions.open_settings(key)
        return {"ok": True}

    # ── models ────────────────────────────────────────────────────────────
    @r.get("/api/setup/models")
    async def models():
        return sup.models.status()

    @r.post("/api/setup/models/retry")
    async def models_retry():
        sup.start_downloads()
        return {"ok": True}

    # ── face enrolment ────────────────────────────────────────────────────
    @r.post("/api/setup/camera/start")
    async def camera_start():
        cam = await asyncio.to_thread(sup.setup_camera)
        return {"ok": bool(cam and cam.available), "error": getattr(cam, "error", "") if cam else "camera unavailable",
                "faces": bool(sup.faces and sup.faces.available)}

    @r.post("/api/setup/camera/stop")
    async def camera_stop():
        sup.release_setup_devices()
        return {"ok": True}

    @r.post("/api/setup/face/enroll")
    async def face_enroll():
        if sup.busy.get("enroll"):
            return {"ok": False, "error": "already enrolling"}
        cam = await asyncio.to_thread(sup.setup_camera)
        if not cam or not cam.available:
            return JSONResponse({"ok": False, "error": "The camera isn't available."}, status_code=400)
        if sup.faces is None:
            if not await asyncio.to_thread(sup.models.ensure, "eyes"):
                return JSONResponse({"ok": False, "error": "Vision models are still downloading."}, status_code=400)
            sup.release_setup_devices()
            cam = await asyncio.to_thread(sup.setup_camera)
        name = sup.cfg.get("owner.name", "owner")
        sup.busy["enroll"] = True
        try:
            frames, target, deadline = [], 20, time.time() + 25
            while len(frames) < target and time.time() < deadline:
                f = cam.latest()
                if f is not None:
                    _, dets = await asyncio.to_thread(sup.faces.detect, f)
                    if len(dets) == 1:
                        frames.append(f)
                        bus.emit("enroll", count=len(frames), target=target)
                await asyncio.sleep(0.25)
            n = await asyncio.to_thread(sup.faces.enroll, name, frames) if frames else 0
        finally:
            sup.busy["enroll"] = False
        bus.emit("enroll", done=True, learned=n)
        if not n:
            return JSONResponse({"ok": False, "error": "I couldn't see a single clear face. Try facing the light."},
                                status_code=400)
        return {"ok": True, "samples": n}

    # ── voice & wake word test ────────────────────────────────────────────
    @r.post("/api/setup/speak")
    async def speak(body: dict = Body(default={})):
        sp = await asyncio.to_thread(sup.setup_speaker)
        title = sup.cfg.get("owner.title", "sir")
        text = body.get("text") or f"Good to meet you, {title}. All systems are ready when you are."
        sp.enqueue(text)
        bus.emit("reply", text=text)
        return {"ok": True, "voice": sp.backend.name}

    @r.post("/api/setup/wake-test")
    async def wake_test():
        if sup.busy.get("wake"):
            return {"ok": False, "error": "already listening"}
        if not await asyncio.to_thread(sup.models.ensure, "wake"):
            return JSONResponse({"ok": False, "error": "Wake word model is still downloading."}, status_code=400)
        from jarvis.voice.audio import Microphone, WakeWord

        ww = WakeWord(sup.cfg.get("voice.wake_word", "hey_jarvis"), float(sup.cfg.get("voice.wake_threshold", 0.5)),
                      models_dir=sup.cfg.models_dir)
        if not await asyncio.to_thread(ww.load):
            return JSONResponse({"ok": False, "error": "Wake word engine failed to load."}, status_code=500)
        mic = Microphone(sup.cfg.get("voice.input_device"))
        try:
            await asyncio.to_thread(mic.start, sup.loop)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Microphone unavailable: {exc}"}, status_code=400)
        sup.busy["wake"] = True
        q = mic.subscribe()
        heard, best, end = False, 0.0, time.time() + 20
        try:
            while time.time() < end and not heard:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                scores = ww._model.predict(frame)
                s = max(scores.values()) if scores else 0.0
                best = max(best, float(s))
                if s >= ww.threshold:
                    heard = True
        finally:
            mic.stop()
            sup.busy["wake"] = False
        bus.emit("wake_test", heard=heard, best=round(best, 2))
        return {"ok": heard, "best": round(best, 2)}

    # ── login item, move to Applications ──────────────────────────────────
    @r.post("/api/setup/login-item")
    async def login(body: dict = Body(...)):
        from jarvis.mac import login_item

        st = await asyncio.to_thread(login_item.set_enabled, bool(body.get("enabled", True)))
        return {"status": st}

    @r.post("/api/setup/move-to-applications")
    async def move():
        bundle = _app_bundle()
        if bundle is None:
            return JSONResponse({"ok": False, "error": "Not running as an app."}, status_code=400)
        dest = Path("/Applications") / bundle.name
        try:
            if dest.exists():
                shutil.rmtree(dest)
            subprocess.run(["/usr/bin/ditto", str(bundle), str(dest)], check=True, capture_output=True)
            subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Couldn't copy to Applications: {exc}"}, status_code=500)

        def relaunch():
            time.sleep(0.6)
            subprocess.Popen(["/usr/bin/open", "-n", str(dest)])
            time.sleep(0.4)
            os._exit(0)

        asyncio.get_running_loop().run_in_executor(None, relaunch)
        return {"ok": True, "path": str(dest)}

    # ── finish ────────────────────────────────────────────────────────────
    @r.post("/api/setup/finish")
    async def finish(body: dict = Body(default={})):
        if not _has_key():
            return JSONResponse({"ok": False, "error": "JARVIS needs an Anthropic API key first."}, status_code=400)
        missing = [k for k in ("wake", "ears", "voice") if not sup.models.ready(k)]
        if missing:
            return JSONResponse({"ok": False, "error": "Still downloading: " + ", ".join(missing)}, status_code=400)
        _write_user_config(sup.cfg, {"vision": {
            "sentry_mode": bool(body.get("sentry_mode", False)),
            "auto_lock_when_away": bool(body.get("auto_lock_when_away", False)),
        }})
        if "login_item" in body:
            from jarvis.mac import login_item

            await asyncio.to_thread(login_item.set_enabled, bool(body["login_item"]))
        (sup.cfg.home / ".setup-complete").write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
        if sup.jarvis is None:
            asyncio.get_running_loop().call_later(0.8, lambda: asyncio.ensure_future(sup.start_jarvis(greet=True)))
        return {"ok": True, "redirect": "/"}

    @r.post("/api/app/show")
    async def show():
        sup.show_window()
        return {"ok": True}

    return r
