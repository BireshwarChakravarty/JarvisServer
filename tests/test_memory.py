from jarvis.core.memory import Memory


def test_facts_roundtrip(tmp_path):
    m = Memory(tmp_path / "m.db")
    a = m.remember("Takes coffee black", "preference")
    b = m.remember("Sister is called Priya", "person")
    assert m.remember("takes coffee black") == a  # de-duplicated
    hits = m.recall("coffee")
    assert hits and hits[0]["text"] == "Takes coffee black"
    assert m.recall("priya")[0]["id"] == b
    assert m.forget(matching="coffee") == 1
    assert [f["text"] for f in m.all_facts()] == ["Sister is called Priya"]


def test_sessions_kv_and_log(tmp_path):
    m = Memory(tmp_path / "m.db")
    m.start_session("s1")
    m.log_turn("s1", "user", "hello")
    m.log_turn("s1", "assistant", "Good evening.")
    assert [t["role"] for t in m.session_transcript("s1")] == ["user", "assistant"]
    m.end_session("s1", "Said hello.")
    assert m.recent_summaries()[0]["summary"] == "Said hello."
    m.set("k", {"a": 1})
    assert m.get("k") == {"a": 1} and m.get("missing", 5) == 5
    m.log_event("arrival", away_for=900)
    assert m.last_event("arrival")["away_for"] == 900
