"""Motion gestures from synthetic MediaPipe hand landmarks (image coords: x right, y down;
the user's right is the image's left, because the Mac camera isn't mirrored)."""
from jarvis.vision.motion import HandMotion, attention


def hand(x, y, pinch=False):
    """21 landmarks: wrist below the palm centre, middle knuckle above, thumb/index tips apart or together."""
    pts = [(x, y)] * 21
    pts = list(pts)
    pts[0] = (x, y + 0.05)            # wrist
    pts[9] = (x, y - 0.05)            # middle MCP  -> palm size 0.10
    pts[4] = (x - 0.01, y - 0.08)     # thumb tip
    pts[8] = (x - 0.01 if pinch else x + 0.07, y - 0.08 if pinch else y - 0.12)
    return pts


def run(frames, dt=1 / 12):
    m, out = HandMotion(), []
    for i, f in enumerate(frames):
        out += m.update(i * dt, f).events
    return m, out


def test_swipe_right_from_the_users_point_of_view():
    _, ev = run([hand(0.70 - 0.06 * i, 0.5) for i in range(6)])   # image x falls = user's right
    assert [(e.kind, e.direction) for e in ev] == [("swipe", "right")]


def test_swipe_left_and_up():
    _, ev = run([hand(0.30 + 0.06 * i, 0.5) for i in range(6)])
    assert [(e.kind, e.direction) for e in ev] == [("swipe", "left")]
    _, ev = run([hand(0.5, 0.75 - 0.07 * i) for i in range(6)])
    assert [(e.kind, e.direction) for e in ev] == [("swipe", "up")]


def test_slow_drift_and_diagonals_are_not_swipes():
    _, ev = run([hand(0.70 - 0.01 * i, 0.5) for i in range(40)])   # 0.4 over 3+ seconds
    assert not ev
    _, ev = run([hand(0.70 - 0.05 * i, 0.3 + 0.05 * i) for i in range(6)])
    assert not ev


def test_one_swipe_per_sweep():
    _, ev = run([hand(0.85 - 0.06 * i, 0.5) for i in range(12)])
    assert sum(e.kind == "swipe" for e in ev) == 1


def test_pinch_drag_up_is_a_volume_dial_and_release_is_not_a_swipe():
    frames = [hand(0.5, 0.6)] * 2 + [hand(0.5, 0.6, True)] * 3
    frames += [hand(0.5, 0.6 - 0.03 * i, True) for i in range(1, 9)]     # drag up 0.24
    frames += [hand(0.5, 0.36 - 0.06 * i) for i in range(1, 5)]          # let go and keep moving fast
    _, ev = run(frames)
    kinds = [e.kind for e in ev]
    assert kinds[0] == "pinch_start" and "pinch_end" in kinds and "swipe" not in kinds
    drags = [e for e in ev if e.kind == "pinch_drag"]
    assert drags and all(e.direction == "y" for e in drags) and drags[-1].total > 0.2
    end = next(e for e in ev if e.kind == "pinch_end")
    assert end.direction == "y" and abs(end.total - 0.24) < 0.02


def test_pinch_drag_sideways_spins():
    frames = [hand(0.5, 0.5, True)] * 3 + [hand(0.5 - 0.02 * i, 0.5, True) for i in range(1, 8)]
    _, ev = run(frames)
    drags = [e for e in ev if e.kind == "pinch_drag"]
    assert drags and all(e.direction == "x" and e.dx > 0 for e in drags)   # user's right


def test_losing_the_hand_ends_a_pinch():
    _, ev = run([hand(0.5, 0.5, True)] * 3 + [None])
    assert [e.kind for e in ev] == ["pinch_start", "pinch_end"]


def test_held_poses_only_count_when_still():
    m, _ = run([hand(0.5, 0.5)] * 6)
    assert m.still()
    m, _ = run([hand(0.5 + 0.04 * i, 0.5) for i in range(6)])
    assert not m.still()


def test_attention():
    straight = [(40, 50), (60, 50), (50, 60)]          # nose midway between the eyes
    turned = [(40, 50), (60, 50), (59, 60)]
    assert attention(straight, 50) > 0.9 and attention(turned, 50) < 0.1
    assert attention([(48, 50), (52, 50), (50, 60)], 50) == 0.0   # profile


def test_engine_maps_swipes_to_actions():
    from pathlib import Path

    from jarvis.vision.gestures import GestureEngine

    got = []
    eng = GestureEngine(Path("."), lambda g, a: got.append((g, a)))
    for i in range(6):
        eng.handle_motion(i / 12, hand(0.70 - 0.06 * i, 0.5))
    assert got == [("Swipe_right", "next")]
