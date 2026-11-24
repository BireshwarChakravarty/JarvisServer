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

from jarvis.aio import spawn
from jarvis.core import themes
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
            got = yaml.safe_load(p.read_text()) or {}
            return got if isinstance(got, dict) else {}
        except Exception:
            return {}
    return {}


def _write_user_config(cfg, patch: dict) -> dict:
    from jarvis.config import deep_merge

    data = deep_merge(_read_user_config(cfg), patch)
    path = cfg.user_config_path
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    tmp.replace(path)                     # all or nothing: a crash mid-write never leaves half a file
    # keep the live config in step
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cfg.data.get(k), dict):
            cfg.data[k] = deep_merge(cfg.data[k], v)
        else:
            cfg.data[k] = v
    return data


def recommended_model(subscription: str) -> str:
    """Pro's allowance is the smallest, and Sonnet answers fastest: a better voice assistant there.
    Max has room for Opus, so leave its default alone."""
    sub = (subscription or "").lower()
    return "sonnet" if "pro" in sub and "max" not in sub else ""


def _current_voice(cfg) -> str:
    return str(cfg.get("voice.tts.voice") or "jarvis")


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
            "provider": cfg.get("brain.provider", "anthropic"),
            "permissions": perms,
            "models": sup.models.status(),
            "face_enrolled": enrolled,
            "login_item": await asyncio.to_thread(login_item.status),
            "app_path": str(bundle) if bundle else None,
            "in_applications": bool(bundle and str(bundle).startswith(("/Applications/", str(Path.home() / "Applications")))),
            "signing": await asyncio.to_thread(permissions.signing_kind, bundle),
            "options": {
                "sentry_mode": bool(cfg.get("vision.sentry_mode", False)),
                "auto_lock_when_away": bool(cfg.get("vision.auto_lock_when_away", False)),
                "hand_control": bool(sup.jarvis is not None and getattr(sup.jarvis, "gestures", None) is not None
                                     and sup.jarvis.gestures.enabled),
                "voice": cfg.get("voice.tts.voice", "bm_george"),
            },
            "theme": themes.current(cfg),
            "themes": themes.listing(),
            "clock": themes.clock_setting(cfg),
            "build": __import__("jarvis").build(),
            "version": __import__("jarvis").version(),
            "clock24": themes.clock_24h(cfg),
        }

    @r.post("/api/setup/clock")
    async def set_clock(body: dict = Body(...)):
        mode = str(body.get("mode", "auto"))
        if mode not in ("24", "12", "auto"):
            return JSONResponse({"ok": False, "error": "mode is 24, 12 or auto"}, status_code=400)
        shown = await asyncio.to_thread(themes.set_clock, sup.cfg, mode)
        return {"ok": True, "clock": mode, "clock24": shown}

    @r.post("/api/setup/theme")
    async def set_theme(body: dict = Body(...)):
        key = themes.normalize(str(body.get("theme", "")))
        if key is None:
            return JSONResponse({"ok": False, "error": "unknown theme"}, status_code=400)
        themes.apply(sup.cfg, key)
        return {"ok": True, "theme": key}

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

    # ── the brain: your Claude or ChatGPT plan ───────────────────────────
    @r.get("/api/setup/brain")
    async def brain_state():
        from jarvis.app import brain_setup

        cfg = sup.cfg
        claude, codex = await asyncio.gather(
            asyncio.to_thread(brain_setup.status, "claude", cfg.get("brain.claude_path")),
            asyncio.to_thread(brain_setup.status, "codex", cfg.get("brain.codex_path")))
        return {"provider": cfg.get("brain.provider", "anthropic"), "claude": claude, "codex": codex,
                "has_key": _has_key(), "claude_model": cfg.get("brain.claude_model") or "",
                "speed": cfg.get("brain.speed", "balanced") or "balanced",
                "recommended_model": recommended_model(claude.get("subscription", ""))}

    @r.post("/api/setup/brain")
    async def brain_choose(body: dict = Body(...)):
        provider = body.get("provider")
        if provider not in ("claude-code", "codex", "anthropic"):
            return JSONResponse({"ok": False, "error": "unknown provider"}, status_code=400)
        patch = {"brain": {"provider": provider}}
        if "claude_model" in body:
            patch["brain"]["claude_model"] = body.get("claude_model") or None
        if "speed" in body:
            from jarvis.core.speed import SPEEDS

            if body.get("speed") not in SPEEDS:
                return JSONResponse({"ok": False, "error": "unknown speed"}, status_code=400)
            patch["brain"]["speed"] = body["speed"]
        _write_user_config(sup.cfg, patch)
        if ("speed" in body or "claude_model" in body) and sup.jarvis is not None and getattr(sup.jarvis, "brain", None):
            try:
                sup.jarvis.brain.reset()        # the next reply starts with the new model and effort
            except Exception:
                pass
        return {"ok": True}

    @r.post("/api/setup/brain/{which}/{action}")
    async def brain_cli(which: str, action: str, body: dict = Body(default={})):
        from jarvis.app import brain_setup

        if which not in ("claude", "codex") or action not in ("install", "login", "input", "cancel"):
            return JSONResponse({"ok": False}, status_code=404)
        key = f"cli_{which}"
        if action == "input":
            sess = getattr(sup, key, None)
            if sess:
                await sess.send(str(body.get("text", "")))
            return {"ok": bool(sess)}
        if action == "cancel":
            sess = getattr(sup, key, None)
            if sess:
                sess.cancel()
            return {"ok": True}
        sess = brain_setup.CLISession(which, action)
        try:
            await sess.start()
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        setattr(sup, key, sess)
        try:
            await asyncio.wait_for(sess.done.wait(), timeout=600 if action == "install" else 900)
        except TimeoutError:
            sess.cancel()
        st = await asyncio.to_thread(brain_setup.status, which)
        return {"ok": sess.code == 0, "code": sess.code, "status": st, "log": sess.lines[-20:]}

    @r.post("/api/setup/brain/test")
    async def brain_test():
        """One real round-trip through the chosen brain."""
        from jarvis.core.cli_brains import make_brain
        from jarvis.tools.registry import ToolRegistry

        brain = make_brain(sup.cfg, ToolRegistry(), None, None)
        text: list[str] = []

        async def collect(s):
            text.append(s)

        try:
            reply = await asyncio.wait_for(brain.respond(
                "This is a connection test. Reply with one short, witty sentence confirming you're online.",
                on_sentence=collect), timeout=120)
        except TimeoutError:
            reply = ""
        finally:
            stop = getattr(brain, "_stop", None)
            if stop:
                await stop()
            if getattr(brain, "mcp", None):
                await brain.mcp.stop()
        bad = (not reply) or reply.startswith(("Claude Code", "Codex", "I'm not signed in", "We've reached",
                                              "Something went wrong", "My Anthropic API key", "I can't reach"))
        return {"ok": not bad, "reply": reply}

    @r.post("/api/setup/open-url")
    async def open_url(body: dict = Body(...)):
        url = body.get("url", "")
        if url.startswith(("https://console.anthropic.com", "https://platform.claude.com", "x-apple.systempreferences:")):
            await asyncio.to_thread(subprocess.run, ["/usr/bin/open", url], capture_output=True)
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

    # ── devices ───────────────────────────────────────────────────────────
    @r.get("/api/setup/devices")
    async def devices():
        from jarvis.mac import devices as dv

        mics, cams = await asyncio.gather(asyncio.to_thread(dv.microphones), asyncio.to_thread(dv.cameras))
        return {"microphones": mics, "cameras": cams,
                "input_device": sup.cfg.get("voice.input_device"),
                "camera_index": int(sup.cfg.get("vision.camera_index", 0))}

    @r.post("/api/setup/devices")
    async def set_devices(body: dict = Body(...)):
        patch: dict = {}
        if "input_device" in body:
            patch.setdefault("voice", {})["input_device"] = body["input_device"] or None
        if "camera_index" in body:
            patch.setdefault("vision", {})["camera_index"] = int(body["camera_index"])
            sup.release_setup_devices()
        if patch:
            _write_user_config(sup.cfg, patch)
        return {"ok": True}

    # ── voice choice ──────────────────────────────────────────────────────
    @r.get("/api/setup/voices")
    async def voices():
        from jarvis.voice import qwen_tts
        from jarvis.voice.tts import VOICE_PRESETS

        natural_ok = await asyncio.to_thread(qwen_tts.supported)
        j = sup.jarvis
        return {
            "current": _current_voice(sup.cfg),
            "neural_ready": sup.models.ready("voice"),
            # the natural voices: an instant sample plays in the page; the model downloads when one is chosen
            "natural": [{"id": k, "label": v["label"], "about": v["about"], "sample": f"/static/voices/{k}.mp3"}
                        for k, v in qwen_tts.HUMAN_VOICES.items()] if natural_ok else [],
            "natural_ready": sup.models.ready("human"),
            "natural_status": (getattr(j, "capabilities", {}) or {}).get("natural_voice") if j else None,
            "natural_force": bool(sup.cfg.get("voice.tts.natural_force", False)),
            "presets": [{"id": k, "label": v["label"], "about": v["about"]} for k, v in VOICE_PRESETS.items()],
        }

    @r.post("/api/setup/voice")
    async def set_voice(body: dict = Body(...)):
        from jarvis.voice import qwen_tts
        from jarvis.voice.tts import VOICE_PRESETS

        vid = str(body.get("voice") or "")
        if vid in qwen_tts.HUMAN_VOICES:
            _write_user_config(sup.cfg, {"voice": {"tts": {"backend": "auto", "voice": vid}}})
            sup.models.want("human")
            sup.start_downloads()                         # fetches the natural voice if it isn't here yet
            j = sup.jarvis
            if j is not None:
                j.cfg.data.setdefault("voice", {}).setdefault("tts", {})["voice"] = vid
                spawn(j.upgrade_voice())                  # switches as soon as it's ready
            return {"ok": True, "current": vid, "natural": True, "ready": sup.models.ready("human")}
        if vid in VOICE_PRESETS:
            tts = {"backend": "auto", "voice": vid}
        else:
            return JSONResponse({"ok": False, "error": "unknown voice"}, status_code=400)
        _write_user_config(sup.cfg, {"voice": {"tts": tts}})
        return {"ok": True, "current": _current_voice(sup.cfg)}

    @r.post("/api/setup/natural-force")
    async def natural_force(body: dict = Body(...)):
        """Use the natural voice even if this Mac runs it a little slower than real time."""
        on = bool(body.get("enabled"))
        _write_user_config(sup.cfg, {"voice": {"tts": {"natural_force": on}}})
        j = sup.jarvis
        if j is not None:
            j.cfg.data.setdefault("voice", {}).setdefault("tts", {})["natural_force"] = on
            if on:
                spawn(j.upgrade_voice())
        return {"ok": True, "enabled": on}

    @r.post("/api/setup/hand-control")
    async def hand_control(body: dict = Body(...)):
        """Live switch (nothing saved: hand control always starts off). The air mouse inside it needs
        Accessibility to move the pointer, so ask for it the first time."""
        from jarvis.mac import permissions

        enabled = bool(body.get("enabled"))
        j = sup.jarvis
        if j is None:
            return JSONResponse({"ok": False, "error": "JARVIS isn't running yet."}, status_code=400)
        message = j.set_hand_control(enabled)
        on = bool(getattr(j, "gestures", None) is not None and j.gestures.enabled)
        needs = False
        if on:
            access = await asyncio.to_thread(permissions.status, "accessibility")
            if access not in ("granted", "unavailable"):
                await asyncio.to_thread(permissions.request, "accessibility")
                needs = True
        return {"ok": True, "enabled": on, "message": message, "needs_permission": needs}

    # ── iPhone / iPad companion ───────────────────────────────────────────
    @r.get("/api/remote")
    async def remote_state():
        from jarvis.remote.server import PORT, lan_addresses, tailscale_address

        enabled = bool(sup.cfg.get("app.companion", False))
        out = {"enabled": enabled, "running": sup.remote.running, "error": sup.remote.error,
               "voice": {"running": bool(getattr(sup.remote, "secure_running", False)),
                         "error": getattr(sup.remote, "secure_error", "")},
               "devices": sup.devices.list()}
        if enabled and sup.remote.running:
            name, ip = await asyncio.to_thread(lan_addresses)
            code, expires = sup.devices.current_code()
            ts_name, ts_ip = await asyncio.to_thread(tailscale_address)
            ts_host = ts_name or ts_ip
            out.update(url=f"http://{name}:{PORT}/remote#pair={code}",
                       ip_url=f"http://{ip}:{PORT}/remote#pair={code}" if ip else None,
                       host=name, expires=int(expires),
                       # away from home: the same companion through your Tailscale network
                       anywhere={"available": bool(ts_host), "host": ts_host,
                                 "url": f"http://{ts_host}:{PORT}/remote#pair={code}" if ts_host else None})
        return out

    @r.post("/api/remote/enable")
    async def remote_enable(body: dict = Body(...)):
        enabled = bool(body.get("enabled"))
        _write_user_config(sup.cfg, {"app": {"companion": enabled}})
        if enabled:
            ok = await sup.remote.start()
            if not ok:
                return JSONResponse({"ok": False, "error": sup.remote.error}, status_code=500)
        else:
            await sup.remote.stop()
        return {"ok": True, "enabled": enabled}

    @r.post("/api/remote/new-code")
    async def remote_new_code():
        sup.devices.new_code()
        return {"ok": True}

    @r.post("/api/remote/forget")
    async def remote_forget(body: dict = Body(default={})):
        return {"ok": True, "forgotten": sup.devices.forget(body.get("id"))}

    @r.post("/api/setup/open-unlock-settings")
    async def open_unlock_settings():
        # Touch ID & Password, where "Use Apple Watch to unlock" lives (Apple's secure auto-unlock)
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.Touch-ID-Settings.extension"])  # noqa: ASYNC220  (doesn't wait)
        return {"ok": True}

    # ── voice & wake word test ────────────────────────────────────────────
    @r.post("/api/setup/speak")
    async def speak(body: dict = Body(default={})):
        sp = await asyncio.to_thread(sup.setup_speaker)
        title = sup.cfg.get("owner.title", "sir")
        text = body.get("text") or (f"Good evening, {title}. I've run the diagnostics and everything checks out. "
                                    f"Shall I bring the rest of the house online?")
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
                except TimeoutError:
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
        def copy():
            if dest.exists():
                shutil.rmtree(dest)
            subprocess.run(["/usr/bin/ditto", str(bundle), str(dest)], check=True, capture_output=True)
            subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True)

        try:
            await asyncio.to_thread(copy)      # a big bundle takes seconds: keep the window responsive
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
        provider = sup.cfg.get("brain.provider", "anthropic")
        if provider == "anthropic" and not _has_key():
            return JSONResponse({"ok": False, "error": "JARVIS needs an Anthropic API key first."}, status_code=400)
        if provider in ("claude-code", "codex"):
            from jarvis.app import brain_setup

            st = await asyncio.to_thread(brain_setup.status, "claude" if provider == "claude-code" else "codex")
            if not st["logged_in"]:
                name = "Claude" if provider == "claude-code" else "ChatGPT"
                return JSONResponse({"ok": False, "error": f"Sign in with your {name} plan first (Intelligence step)."},
                                    status_code=400)
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
            asyncio.get_running_loop().call_later(0.8, lambda: spawn(sup.start_jarvis(greet=True)))
        return {"ok": True, "redirect": "/"}

    @r.post("/api/app/show")
    async def show():
        sup.show_window()
        return {"ok": True}

    return r
