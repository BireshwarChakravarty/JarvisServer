"""Find things on screen that JARVIS can point at: buttons, menus, fields, tabs, links, any text.

Two ways to see, best first:
  1. Accessibility: the app's own description of its interface (role, title, exact frame). Precise,
     instant and works even when something is partly covered. Needs the Accessibility permission.
     Chromium and Electron apps (Chrome, Slack, VS Code…) only describe web content once asked,
     so JARVIS asks them (AXManualAccessibility).
  2. Text on screen: Apple's Vision text recognition on a screenshot, for anything Accessibility
     doesn't describe (canvas apps, some web pages, games). Needs Screen Recording.

Coordinates everywhere are global screen points with the origin at the top-left of the main
display, the way Accessibility reports them.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jarvis.mac.system import IS_MAC

log = logging.getLogger("jarvis.screen")

CLICKABLE = {"AXButton", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
             "AXMenuButton", "AXTab", "AXLink", "AXDisclosureTriangle", "AXComboBox", "AXTextField",
             "AXTextArea", "AXSearchField", "AXSlider", "AXIncrementor", "AXCell", "AXRow", "AXSegment",
             "AXColorWell", "AXDockItem", "AXSwitch", "AXToolbarButton", "AXStaticText", "AXImage", "AXHeading"}
ROLE_WORDS = {
    "AXButton": "button", "AXMenuItem": "menu item", "AXMenuBarItem": "menu", "AXCheckBox": "checkbox",
    "AXRadioButton": "option", "AXPopUpButton": "pop-up menu", "AXMenuButton": "menu button", "AXTab": "tab",
    "AXLink": "link", "AXTextField": "text field", "AXTextArea": "text area", "AXSearchField": "search field",
    "AXComboBox": "combo box", "AXSlider": "slider", "AXCell": "cell", "AXRow": "row", "AXStaticText": "text",
    "AXImage": "image", "AXHeading": "heading", "AXSwitch": "switch", "AXDisclosureTriangle": "disclosure",
}
STOP = {"the", "a", "an", "to", "on", "in", "of", "for", "my", "me", "click", "press", "tap", "find", "where",
        "is", "button", "menu", "item", "option", "icon", "field", "box", "please", "show", "that", "this", "it"}


@dataclass
class Element:
    id: str
    role: str
    label: str
    x: float
    y: float
    w: float
    h: float
    source: str = "ax"           # ax | text
    app: str = ""
    area: str = ""               # "menu bar", "window", "menu", "toolbar"…
    enabled: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return ROLE_WORDS.get(self.role, self.role.removeprefix("AX").lower() or "item")

    def public(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        d["kind"] = self.kind
        d["frame"] = [round(self.x), round(self.y), round(self.w), round(self.h)]
        for k in ("x", "y", "w", "h"):
            d.pop(k)
        return d


# ── ranking (pure: unit-tested) ─────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower().replace("…", " ").replace("...", " ")).strip()


def _words(s: str) -> list[str]:
    return [w for w in _norm(s).split() if w and w not in STOP]


def score(el: Element, query: str) -> float:
    """How well an element answers "point at <query>". 0 = not at all."""
    q, label = _norm(query), _norm(el.label)
    if not label or el.w < 2 or el.h < 2:
        return 0.0
    qw = _words(query) or q.split()
    lw = set(label.split())
    s = 0.0
    if label == q or label == " ".join(qw):
        s += 10
    elif q and (label.startswith(q) or q in label):
        s += 6
    hits = 0.0
    for w in qw:
        if w in lw or (len(w) >= 3 and any(x.startswith(w) for x in lw)):
            hits += 1
        elif len(w) >= 4 and any(x.startswith(w[:4]) for x in lw):      # "prefs" → Preferences
            hits += 0.5
    if not hits and s == 0:
        return 0.0
    s += 4 * hits / max(1, len(qw))
    s -= 0.25 * max(0, len(lw) - len(qw))                 # long labels that merely contain the word rank lower
    if el.role in ("AXButton", "AXMenuItem", "AXMenuBarItem", "AXTab", "AXLink", "AXCheckBox", "AXRadioButton",
                   "AXPopUpButton", "AXMenuButton", "AXSegment", "AXSwitch"):
        s += 1.5
    elif el.role in ("AXStaticText", "AXImage", "AXHeading"):
        s -= 0.5
    if "button" in query.lower() and el.role in ("AXButton", "AXToolbarButton"):
        s += 1
    if "menu" in query.lower() and el.role in ("AXMenuBarItem", "AXMenuItem", "AXMenuButton", "AXPopUpButton"):
        s += 1
    if not el.enabled:
        s -= 2
    if el.source == "text":
        s -= 0.75                                           # prefer the app's own description when both exist
    return round(s, 3)


def rank(elements: list[Element], query: str, limit: int = 5) -> list[tuple[Element, float]]:
    scored = [(e, score(e, query)) for e in elements]
    scored = [x for x in scored if x[1] > 0]
    scored.sort(key=lambda x: (-x[1], x[0].y, x[0].x))
    out, seen = [], set()
    for e, s in scored:                                     # one entry per place on screen
        key = (round(e.x / 6), round(e.y / 6), _norm(e.label))
        if key in seen:
            continue
        seen.add(key)
        out.append((e, s))
        if len(out) >= limit:
            break
    return out


# ── Accessibility (macOS) ───────────────────────────────────────────────
def _ax():
    import ApplicationServices as AS

    return AS


def _attr(el, name):
    AS = _ax()
    try:
        err, val = AS.AXUIElementCopyAttributeValue(el, name, None)
        return val if err == 0 else None
    except Exception:
        return None


def _frame(el) -> tuple[float, float, float, float] | None:
    AS = _ax()
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return None
    try:
        ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        if ok1 and ok2:
            return float(p.x), float(p.y), float(s.width), float(s.height)
    except Exception:
        pass
    return None


def _label(el, role: str) -> str:
    for name in ("AXTitle", "AXDescription", "AXLabel", "AXHelp", "AXPlaceholderValue", "AXIdentifier"):
        v = _attr(el, name)
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    if role in ("AXStaticText", "AXHeading", "AXCell", "AXTextField", "AXLink", "AXMenuItem"):
        v = _attr(el, "AXValue")
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    title_el = _attr(el, "AXTitleUIElement")
    if title_el is not None:
        v = _attr(title_el, "AXValue") or _attr(title_el, "AXTitle")
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    return ""


def frontmost() -> tuple[int, str]:
    """The app you're working in: the frontmost one that isn't JARVIS himself. (You're often looking
    at his window when you ask "how do I…", but you mean the app behind it.)"""
    import os

    from AppKit import NSRunningApplication, NSWorkspace

    me = os.getpid()
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is not None and int(app.processIdentifier()) != me:
        return int(app.processIdentifier()), str(app.localizedName() or "")
    try:   # the topmost normal window owned by someone else (owner name/pid need no permission)
        import Quartz

        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
        for w in infos or []:
            pid = int(w.get("kCGWindowOwnerPID", 0))
            if w.get("kCGWindowLayer", 1) == 0 and pid and pid != me:
                other = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if other is not None and other.activationPolicy() == 0:      # a regular app
                    return pid, str(other.localizedName() or w.get("kCGWindowOwnerName", ""))
    except Exception as exc:
        log.debug("window list unavailable: %s", exc)
    return (int(app.processIdentifier()), str(app.localizedName() or "")) if app is not None else (me, "")


def ax_elements(pid: int | None = None, app_name: str = "", max_nodes: int = 2500,
                budget_s: float = 2.0) -> list[Element]:
    """Everything with a name and a place in the app's interface: menu bar first, then its windows
    (the focused one first). Bounded by node count and time so a huge app can't stall JARVIS."""
    if not IS_MAC:
        return []
    AS = _ax()
    if pid is None:
        pid, app_name = frontmost()
    app = AS.AXUIElementCreateApplication(pid)
    try:   # Chromium / Electron only expose web content when asked
        AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
    except Exception:
        pass
    roots: list[tuple[object, str]] = []
    mb = _attr(app, "AXMenuBar")
    if mb is not None:
        roots.append((mb, "menu bar"))
    focused = _attr(app, "AXFocusedWindow")
    if focused is not None:
        roots.append((focused, "window"))
    for w in _attr(app, "AXWindows") or []:
        if focused is None or w != focused:
            roots.append((w, "window"))
    out: list[Element] = []
    deadline = time.monotonic() + budget_s
    stack = [(r, area, 0) for r, area in reversed(roots)]
    seen = 0
    while stack and seen < max_nodes and time.monotonic() < deadline:
        el, area, depth = stack.pop()
        seen += 1
        role = _attr(el, "AXRole") or ""
        if role in CLICKABLE or role.endswith("Button"):
            label = _label(el, role)
            fr = _frame(el) if label else None
            if fr and fr[2] >= 2 and fr[3] >= 2:
                enabled = _attr(el, "AXEnabled")
                sub_area = "menu" if role == "AXMenuItem" else ("toolbar" if area == "window" and
                                                                   _attr(_attr(el, "AXParent"), "AXRole") == "AXToolbar"
                                                                   else area)
                out.append(Element(f"ax:{len(out)}", role, label, *fr, source="ax", app=app_name,
                                   area=sub_area, enabled=enabled is not False))
        if depth < 40:
            kids = _attr(el, "AXVisibleChildren") or _attr(el, "AXChildren") or []
            # a closed menu's items have no place on screen: skip them unless the menu is open
            if role == "AXMenuBarItem" and not _attr(el, "AXSelected"):
                kids = []
            for k in reversed(list(kids)[:400]):
                stack.append((k, area, depth + 1))
    log.debug("ax: %d nodes, %d elements for %s", seen, len(out), app_name)
    return out


# ── text on screen (Vision) ─────────────────────────────────────────────
def screens() -> list[tuple[float, float, float, float]]:
    """Each display's frame as (x, y, w, h) in global top-left points; main display first."""
    from AppKit import NSScreen

    all_ = list(NSScreen.screens())
    if not all_:
        return []
    main_h = all_[0].frame().size.height
    out = []
    for s in all_:
        f = s.frame()
        out.append((float(f.origin.x), float(main_h - f.origin.y - f.size.height),
                    float(f.size.width), float(f.size.height)))
    return out


def vision_box_to_global(box: tuple[float, float, float, float], screen: tuple[float, float, float, float]):
    """Vision's normalised box (origin bottom-left) → global top-left points."""
    bx, by, bw, bh = box
    sx, sy, sw, sh = screen
    return sx + bx * sw, sy + (1.0 - by - bh) * sh, bw * sw, bh * sh


def recognise_text(image_path: str | Path) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """[(text, normalised box, confidence)] from Apple's Vision text recogniser."""
    import Vision
    from Foundation import NSURL

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(image_path)), None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"text recognition failed: {err}")
    out = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        b = obs.boundingBox()
        out.append((str(cand[0].string()), (b.origin.x, b.origin.y, b.size.width, b.size.height),
                    float(cand[0].confidence())))
    return out


def text_elements(app_name: str = "") -> list[Element]:
    """Words on every display, located by Vision. Needs Screen Recording."""
    if not IS_MAC:
        return []
    frames = screens()
    with tempfile.TemporaryDirectory() as tmp:
        files = [str(Path(tmp) / f"d{i}.png") for i in range(len(frames))]
        subprocess.run(["screencapture", "-x", *files], capture_output=True, timeout=15)
        out: list[Element] = []
        for f, fr in zip(files, frames):
            if not Path(f).exists():
                continue
            for text, box, conf in recognise_text(f):
                if conf < 0.3:
                    continue
                x, y, w, h = vision_box_to_global(box, fr)
                out.append(Element(f"text:{len(out)}", "AXStaticText", text[:120], x, y, w, h, source="text",
                                   app=app_name, area="screen"))
        return out


# ── the one call JARVIS uses ────────────────────────────────────────────
_LAST: dict[str, Element] = {}


def find(query: str, limit: int = 5, use_text: bool = True) -> list[dict]:
    """Best matches for `query` in the frontmost app (then on screen as text)."""
    pid, app = frontmost() if IS_MAC else (None, "")
    els = ax_elements(pid, app)
    hits = rank(els, query, limit)
    if use_text and (not hits or hits[0][1] < 5):
        try:
            hits = rank(els + text_elements(app), query, limit)
        except Exception as exc:
            log.info("text recognition unavailable: %s", exc)
    _LAST.clear()
    out = []
    for e, s in hits:
        _LAST[e.id] = e
        out.append({**e.public(), "score": s})
    return out


def resolve(target: str) -> Element | None:
    """An id from the last find(), or a description to find now."""
    if target in _LAST:
        return _LAST[target]
    got = find(target, limit=1)
    return _LAST.get(got[0]["id"]) if got else None
