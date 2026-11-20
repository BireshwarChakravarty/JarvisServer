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


def test_forget_takes_your_words_literally(tmp_path):
    from jarvis.core.memory import Memory

    m = Memory(tmp_path / "m.db")
    for f in ("Likes 50% dark chocolate", "Prefers tea", "Has a dog called Rex", "Works at Stark Industries"):
        m.remember(f)
    assert m.forget(matching="%") == 0 and m.forget(matching="a") == 0     # no wildcards, nothing tiny
    assert m.forget(matching="50%") == 1
    assert len(m.all_facts()) == 3
    assert [f["text"] for f in m.recall("_")] == []


def test_a_damaged_memory_file_doesnt_stop_jarvis(tmp_path):
    from jarvis.core.memory import Memory

    p = tmp_path / "memory.db"
    p.write_bytes(b"this is not a database at all" * 100)
    m = Memory(p)
    m.remember("Starts fresh")
    assert [f["text"] for f in m.all_facts()] == ["Starts fresh"]
    assert list(tmp_path.glob("memory.damaged-*.db"))                       # the old file kept aside
