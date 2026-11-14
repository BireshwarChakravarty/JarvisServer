"""J.A.R.V.I.S. command line.

  jarvis                 start JARVIS (voice + camera + HUD)
  jarvis setup           first-time setup wizard
  jarvis enroll          teach JARVIS your face
  jarvis doctor          check everything is wired up
  jarvis chat            text chat in the terminal
  jarvis say "text"      speak with JARVIS's voice
  jarvis install         start JARVIS at login (JARVIS.app + Login Item)
  jarvis uninstall       remove the login item / launch agent
  jarvis hud --demo      preview the HUD with simulated data
  jarvis devices         list audio devices
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jarvis import __version__
from jarvis.config import jarvis_home, load_config

CYAN, AMBER, RED, GREEN, DIM, RESET = "\033[96m", "\033[93m", "\033[91m", "\033[92m", "\033[2m", "\033[0m"

BANNER = f"""{CYAN}
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝{RESET}
{DIM}   Just A Rather Very Intelligent System · v{__version__}{RESET}
"""


def setup_logging(verbose: bool = False) -> None:
    home = jarvis_home()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(home / "jarvis.log", maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr.isatty() or verbose:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.DEBUG if verbose else logging.INFO)
        root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "anthropic", "uvicorn", "asyncio", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def wait_for_port(host: str, port: int, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_in_use(host, port):
            return True
        time.sleep(0.2)
    return False


# ═══════════════════════════════ run ═════════════════════════════════════════
def cmd_run(args) -> None:
    cfg = load_config(args.config)
    setup_logging(args.verbose)
    host, port = cfg.get("hud.host", "127.0.0.1"), int(cfg.get("hud.port", 7777))
    hud_on = bool(cfg.get("hud.enabled", True)) and not args.no_hud
    url = f"http://{host}:{port}/"
    if hud_on and port_in_use(host, port):
        print(f"{AMBER}JARVIS already appears to be running — opening the HUD.{RESET}")
        webbrowser.open(url)
        return

    from jarvis.core.orchestrator import Jarvis
    from jarvis.hud import window

    print(BANNER)
    app = Jarvis(cfg, headless=args.text)

    async def main_async() -> None:
        jobs = [app.run(greet=not args.no_greet)]
        if hud_on:
            from jarvis.hud.server import create_app, serve

            jobs.append(serve(create_app(app), host, port))
        await asyncio.gather(*jobs)

    use_window = hud_on and cfg.get("hud.window", True) and not args.browser and window.available()
    if use_window:
        worker = threading.Thread(target=lambda: asyncio.run(main_async()), daemon=True, name="jarvis-core")
        worker.start()
        if not wait_for_port(host, port):
            print(f"{RED}HUD server didn't come up; check ~/.jarvis/jarvis.log{RESET}")
        window.run_window(url, cfg)  # blocks until the window is closed
        print("HUD closed — JARVIS shutting down.")
        os._exit(0)
    if hud_on and not args.no_browser:
        threading.Thread(target=lambda: wait_for_port(host, port) and webbrowser.open(url), daemon=True).start()
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nGoodbye, sir.")


# ═══════════════════════════════ chat ════════════════════════════════════════
def cmd_chat(args) -> None:
    cfg = load_config(args.config, overrides={"vision": {"enabled": False}})
    setup_logging(args.verbose)
    from jarvis.core.orchestrator import Jarvis

    async def main() -> None:
        app = Jarvis(cfg, headless=not args.speak)
        await app.start()
        if app.mic:  # chat mode never listens
            app.mic.stop()
            app.mic = None

        async def terminal_confirm(action: str) -> bool:
            answer = await asyncio.to_thread(input, f"{AMBER}  ⚠ Shall I {action}? [y/N] {RESET}")
            return answer.strip().lower() in ("y", "yes")

        app.ctx.confirm = terminal_confirm
        print(BANNER + f"{DIM}Type to talk to JARVIS. Ctrl-D to exit.{RESET}\n")
        from jarvis.events import bus

        q = bus.subscribe()

        async def show_tools() -> None:
            while True:
                e = await q.get()
                if e["type"] == "tool" and e.get("status") == "start":
                    print(f"{DIM}  ⚙ {e['name']} {e.get('args') or ''}{RESET}")

        asyncio.create_task(show_tools())
        while True:
            try:
                text = await asyncio.to_thread(input, f"{AMBER}you ›{RESET} ")
            except EOFError:
                print()
                break
            if not text.strip():
                continue
            print(f"{CYAN}jarvis ›{RESET} ", end="", flush=True)

            async def on_sentence(s: str) -> None:
                print(s, end=" ", flush=True)
                if args.speak and app.speaker:
                    app.speaker.enqueue(s)

            await app.brain.respond(text, on_sentence=on_sentence, context=await app._live_context())
            print()
        app.brain.reset()
        await asyncio.sleep(0.5)

    asyncio.run(main())


# ═══════════════════════════════ setup ═══════════════════════════════════════
def _ask(prompt: str, default: str = "") -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    value = input(f"{CYAN}?{RESET} {prompt}{suffix}: ").strip()
    return value or default


def cmd_setup(args) -> None:
    import yaml

    print(BANNER)
    home = jarvis_home()
    cfg_path = home / "config.yaml"
    existing = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    existing = existing or {}
    print(f"Let's get you set up. Everything is stored in {home}\n")
    name = _ask("What should I call you (first name)?", existing.get("owner", {}).get("name") or getpass.getuser().title())
    title = _ask("How should I address you? (sir / ma'am / boss / your name)", existing.get("owner", {}).get("title", "sir"))
    city = _ask("Which city are you in? (for weather)", existing.get("location", {}).get("city", "London"))
    units = _ask("Units — metric or imperial?", existing.get("location", {}).get("units", "metric"))
    existing.setdefault("owner", {}).update(name=name, title=title)
    existing.setdefault("location", {}).update(city=city, units=units)
    cfg_path.write_text(yaml.safe_dump(existing, sort_keys=False))
    print(f"{GREEN}✓{RESET} Saved {cfg_path}")

    env_path = home / ".env"
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY")) or (env_path.exists() and "ANTHROPIC_API_KEY" in env_path.read_text())
    if not have_key:
        print(f"\nJARVIS's brain is Claude. Get an API key at {CYAN}https://console.anthropic.com/settings/keys{RESET}")
        key = getpass.getpass(f"{CYAN}?{RESET} Paste your Anthropic API key (hidden, Enter to skip): ").strip()
        if key:
            env_path.write_text(f"ANTHROPIC_API_KEY={key}\n")
            env_path.chmod(0o600)
            print(f"{GREEN}✓{RESET} Key saved to {env_path} (readable only by you)")
    else:
        print(f"{GREEN}✓{RESET} Anthropic API key found")

    print(f"\n{DIM}Pre-downloading voice & vision models (one-off, a few hundred MB)…{RESET}")
    cfg = load_config()
    _prefetch(cfg)
    if sys.platform == "darwin" and _ask("\nStart JARVIS automatically when you log in? (y/n)", "y").lower().startswith("y"):
        cmd_install(argparse.Namespace(launchd=False))
    if _ask("Teach JARVIS your face now? (y/n)", "y").lower().startswith("y"):
        cmd_enroll(argparse.Namespace(name=name, config=None, samples=20))
    print(f"\n{GREEN}All set.{RESET} Run {CYAN}jarvis{RESET} — and say {CYAN}“Hey Jarvis”{RESET}.\n")


def _prefetch(cfg) -> None:
    steps = []
    try:
        from jarvis.voice.audio import WakeWord

        steps.append(("Wake word", lambda: WakeWord(cfg.get("voice.wake_word")).load()))
    except Exception:
        pass
    try:
        from jarvis.voice.stt import Transcriber

        steps.append(("Speech recognition", lambda: Transcriber(cfg.get("voice.stt.backend"), cfg.get("voice.stt.model")).warmup() or True))
    except Exception:
        pass
    from jarvis.voice.tts import make_backend

    steps.append(("Voice", lambda: make_backend(cfg.get("voice.tts.backend"), cfg.get("voice.tts.voice"), 1.0,
                                                cfg.get("voice.tts.say_voice"), cfg.models_dir).name))
    from jarvis.vision.faces import FaceEngine

    steps.append(("Face models", lambda: FaceEngine(cfg.models_dir, cfg.faces_dir).load()))
    from jarvis.vision.gestures import GestureEngine

    steps.append(("Gesture model", lambda: GestureEngine(cfg.models_dir, lambda *a: None).load()))
    for label, fn in steps:
        try:
            ok = fn()
            print(f"  {GREEN if ok else AMBER}{'✓' if ok else '•'}{RESET} {label}{'' if ok else ' (unavailable — see jarvis doctor)'}")
        except Exception as exc:
            print(f"  {AMBER}•{RESET} {label}: {exc}")


# ═══════════════════════════════ enroll ══════════════════════════════════════
def cmd_enroll(args) -> None:
    cfg = load_config(getattr(args, "config", None))
    name = args.name or cfg.get("owner.name", "owner")
    from jarvis.vision.camera import Camera
    from jarvis.vision.faces import FaceEngine

    engine = FaceEngine(cfg.models_dir, cfg.faces_dir)
    if not engine.load():
        print(f"{RED}Face engine unavailable. pip install 'jarvis-mac[vision]'{RESET}")
        return
    cam = Camera(int(cfg.get("vision.camera_index", 0)))
    if not cam.start():
        print(f"{RED}{cam.error}{RESET}")
        return
    print(f"Look at the camera, {name}. Slowly turn your head a little left, right, up and down…")
    frames = []
    time.sleep(1.2)
    target = int(getattr(args, "samples", 20) or 20)
    try:
        import cv2

        show = True
    except ImportError:
        show = False
    t_end = time.time() + 25
    while len(frames) < target and time.time() < t_end:
        f = cam.latest()
        if f is not None:
            _, dets = engine.detect(f)
            if len(dets) == 1:
                frames.append(f)
                print(f"\r  captured {len(frames)}/{target}", end="", flush=True)
            if show:
                try:
                    preview = f.copy()
                    cv2.putText(preview, f"JARVIS enrolment {len(frames)}/{target}", (30, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 230, 25), 2)
                    cv2.imshow("J.A.R.V.I.S. — face enrolment", preview)
                    cv2.waitKey(1)
                except Exception:
                    show = False
        time.sleep(0.25)
    cam.stop()
    if show:
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
    n = engine.enroll(name, frames)
    print()
    if n:
        print(f"{GREEN}✓ Learned {name}'s face from {n} samples.{RESET} Run again any time to improve accuracy.")
    else:
        print(f"{RED}Couldn't see a single clear face. Try better lighting.{RESET}")


# ═══════════════════════════════ doctor ══════════════════════════════════════
def cmd_doctor(args) -> None:
    import importlib
    import platform

    cfg = load_config(args.config)
    print(BANNER)

    def line(ok, label, detail=""):
        mark = f"{GREEN}✓{RESET}" if ok is True else (f"{AMBER}•{RESET}" if ok is None else f"{RED}✗{RESET}")
        print(f"  {mark} {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))

    print("System")
    line(sys.version_info >= (3, 10), f"Python {platform.python_version()}", sys.executable)
    line(platform.system() == "Darwin" or None, f"{platform.system()} {platform.machine()}",
         "" if platform.system() == "Darwin" else "Mac-control features need macOS")
    line(True, f"Data folder {jarvis_home()}", f"config: {cfg.source or 'defaults'}")

    print("\nBrain")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    line(bool(key) or None, "Anthropic credentials", "found" if key else "set ANTHROPIC_API_KEY in ~/.jarvis/.env (jarvis setup)")
    line(True, f"Model {cfg.get('brain.model')} · effort {cfg.get('brain.effort')}")
    if key and args.online:
        try:
            import anthropic

            msg = anthropic.Anthropic().messages.create(model=cfg.get("brain.model"), max_tokens=64,
                                                        messages=[{"role": "user", "content": "Say 'online'."}])
            line(True, "API round-trip", " ".join(b.text for b in msg.content if b.type == "text")[:40])
        except Exception as exc:
            line(False, "API round-trip", str(exc)[:120])

    print("\nModules")
    mods = [
        ("sounddevice", "microphone & speaker", "voice"), ("openwakeword", "“Hey Jarvis” wake word", "voice"),
        ("webrtcvad", "voice activity detection", "voice"), ("mlx_whisper", "speech-to-text on Apple GPU", "apple"),
        ("faster_whisper", "speech-to-text on CPU", "voice"), ("mlx_audio", "Kokoro voice on Apple GPU", "apple"),
        ("kokoro_onnx", "Kokoro voice on CPU", "tts-onnx"), ("cv2", "camera & face recognition", "vision"),
        ("mediapipe", "gesture recognition", "vision"), ("webview", "native HUD window", "ui"),
        ("pynput", "push-to-talk & hotkeys", "ui"), ("Quartz", "lock/unlock detection", "apple"),
        ("EventKit", "Calendar & Reminders", "apple"), ("mcp", "MCP tool servers", "mcp"),
    ]
    for mod, purpose, extra in mods:
        try:
            importlib.import_module(mod)
            line(True, f"{mod:15s} {purpose}")
        except Exception:
            line(None, f"{mod:15s} {purpose}", f"pip install 'jarvis-mac[{extra}]'")

    print("\nDevices")
    try:
        import sounddevice as sd

        line(True, f"Default input: {sd.query_devices(kind='input')['name']}")
        line(True, f"Default output: {sd.query_devices(kind='output')['name']}")
    except Exception as exc:
        line(None, "Audio devices", str(exc)[:80])
    try:
        import cv2

        cap = cv2.VideoCapture(int(cfg.get("vision.camera_index", 0)))
        ok = cap.isOpened()
        cap.release()
        line(ok or None, f"Camera #{cfg.get('vision.camera_index', 0)}",
             "" if ok else "grant Camera access in System Settings › Privacy & Security")
    except Exception:
        pass
    faces = cfg.faces_dir / "embeddings.json"
    line(faces.exists() or None, "Face enrolled", "" if faces.exists() else "run: jarvis enroll")

    if platform.system() == "Darwin":
        print(f"\n{DIM}macOS permissions to grant to JARVIS.app (or your terminal) in System Settings › Privacy & Security:"
              f"\n  Microphone · Camera · Accessibility (push-to-talk, typing) · Automation (Music, Calendar, Messages,"
              f" System Events) · Calendars · Reminders · Contacts{RESET}")


# ═══════════════════════════════ misc ════════════════════════════════════════
def cmd_say(args) -> None:
    cfg = load_config(args.config)
    from jarvis.voice.tts import Speaker, make_backend

    backend = make_backend(cfg.get("voice.tts.backend"), cfg.get("voice.tts.voice"), float(cfg.get("voice.tts.speed", 1.0)),
                           cfg.get("voice.tts.say_voice"), cfg.models_dir)
    sp = Speaker(backend, cfg.get("voice.output_device"))
    sp.start()
    sp.enqueue(" ".join(args.text) or "At your service, sir.")
    time.sleep(0.2)
    sp._idle.wait(120)
    print(f"{DIM}(voice: {backend.name}){RESET}")


def cmd_install(args) -> None:
    if sys.platform != "darwin":
        print("Login integration is macOS only.")
        return
    from jarvis.mac import app_bundle

    if getattr(args, "launchd", False):
        path = app_bundle.install_launch_agent()
        print(f"{GREEN}✓{RESET} LaunchAgent installed: {path}")
        return
    app = app_bundle.build_app()
    print(f"{GREEN}✓{RESET} Built {app}")
    try:
        app_bundle.add_login_item(app)
        print(f"{GREEN}✓{RESET} Added to Login Items — JARVIS will greet you every time you log in.")
    except Exception as exc:
        print(f"{AMBER}Couldn't add the login item automatically ({exc}).{RESET}\n"
              f"  Add it yourself: System Settings › General › Login Items › + › {app}")
    print(f"  Launch it now with: open '{app}'")


def cmd_uninstall(args) -> None:
    from jarvis.mac import app_bundle

    try:
        app_bundle.remove_login_item()
    except Exception:
        pass
    app_bundle.uninstall_launch_agent()
    print(f"{GREEN}✓{RESET} JARVIS will no longer start at login. (~/Applications/JARVIS.app left in place.)")


def cmd_hud(args) -> None:
    cfg = load_config(args.config)
    setup_logging(args.verbose)
    from jarvis.hud.server import create_app, serve

    host, port = cfg.get("hud.host", "127.0.0.1"), int(cfg.get("hud.port", 7777)) + (1 if args.demo else 0)
    url = f"http://{host}:{port}/" + ("?demo" if args.demo else "")
    print(f"HUD preview at {CYAN}{url}{RESET}")
    threading.Thread(target=lambda: wait_for_port(host, port) and webbrowser.open(url), daemon=True).start()
    asyncio.run(serve(create_app(None), host, port))


def cmd_devices(args) -> None:
    import sounddevice as sd

    print(sd.query_devices())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. for macOS")
    parser.add_argument("--config", help="path to a config.yaml (default ~/.jarvis/config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="start JARVIS")
    p.add_argument("--no-hud", action="store_true", help="no interface at all")
    p.add_argument("--browser", action="store_true", help="show the HUD in your browser instead of a window")
    p.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    p.add_argument("--text", action="store_true", help="no mic/camera (HUD text input only)")
    p.add_argument("--no-greet", action="store_true", help="skip the login greeting")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("chat", help="text chat in the terminal")
    p.add_argument("--speak", action="store_true", help="also speak replies")
    p.set_defaults(fn=cmd_chat)
    sub.add_parser("setup", help="first-time setup wizard").set_defaults(fn=cmd_setup)
    p = sub.add_parser("enroll", help="teach JARVIS your face")
    p.add_argument("--name")
    p.add_argument("--samples", type=int, default=20)
    p.set_defaults(fn=cmd_enroll)
    p = sub.add_parser("doctor", help="diagnose the installation")
    p.add_argument("--online", action="store_true", help="also make a test API call")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("say", help="speak text with JARVIS's voice")
    p.add_argument("text", nargs="*")
    p.set_defaults(fn=cmd_say)
    p = sub.add_parser("install", help="start JARVIS at login")
    p.add_argument("--launchd", action="store_true", help="use a LaunchAgent instead of JARVIS.app + Login Item")
    p.set_defaults(fn=cmd_install)
    sub.add_parser("uninstall", help="stop starting at login").set_defaults(fn=cmd_uninstall)
    p = sub.add_parser("hud", help="serve the HUD only")
    p.add_argument("--demo", action="store_true", help="simulated data, no backend")
    p.set_defaults(fn=cmd_hud)
    sub.add_parser("devices", help="list audio devices").set_defaults(fn=cmd_devices)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args.fn = cmd_run
    for attr, default in (("no_hud", False), ("browser", False), ("no_browser", False), ("text", False),
                          ("no_greet", False)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    args.fn(args)


if __name__ == "__main__":
    main()
