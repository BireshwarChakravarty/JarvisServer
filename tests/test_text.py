from jarvis.voice.stt import clean_transcript, strip_wake_phrase
from jarvis.voice.text import SentenceSplitter, clean_for_speech


def feed_all(text, step=3):
    sp = SentenceSplitter()
    out = []
    for i in range(0, len(text), step):
        out += sp.feed(text[i:i + step])
    return out + sp.flush()


def test_splits_streamed_sentences():
    out = feed_all("Good morning, sir. It's 14 degrees outside! Shall we begin? Yes.")
    assert out == ["Good morning, sir.", "It's 14 degrees outside!", "Shall we begin?", "Yes."]


def test_keeps_abbreviations_and_decimals_together():
    out = feed_all("Dr. Banner called at 3.30 today about the e.g. gamma readings. Call back?")
    assert out[0].startswith("Dr. Banner called at 3.30 today")
    assert len(out) == 2


def test_newlines_break_and_long_runs_split():
    out = feed_all("First line\nSecond line\n")
    assert out == ["First line", "Second line"]
    long = "word, " * 60
    assert len(feed_all(long)) > 1


def test_clean_for_speech():
    s = clean_for_speech("**Done.** See [the docs](https://x.io) or https://example.com — it's 21°C & 40% humid.")
    assert "*" not in s and "https" not in s and "21 degrees" in s and "40 percent" in s and " and " in s


def test_wake_phrase_stripping():
    assert strip_wake_phrase("Hey Jarvis, what time is it?") == (True, "what time is it?")
    assert strip_wake_phrase("what's the weather jarvis") == (True, "what's the weather")
    assert strip_wake_phrase("open safari") == (False, "open safari")
    assert clean_transcript(" Thank you. ") == ""
