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


def test_an_en_dash_is_a_pause_in_prose_and_to_in_a_range():
    from jarvis.voice.text import clean_for_speech

    assert clean_for_speech("It's raining – take an umbrella.") == "It's raining, take an umbrella."
    assert clean_for_speech("Open 9–5 on weekdays.") == "Open 9 to 5 on weekdays."
    assert clean_for_speech("From 2 – 3 pm.") == "From 2 to 3 pm."


def test_spoken_polish_drops_assistant_filler_and_extra_sirs():
    from jarvis.voice.text import polish_spoken

    st = {}
    said = [polish_spoken(x, st) for x in ["Certainly! Your meeting is at three, sir.", "The rain stops by five, sir.",
                                             "Sir, the door is locked.", "Is there anything else I can help you with?"]]
    assert said == ["Your meeting is at three, sir.", "The rain stops by five.", "The door is locked.", ""]
    st = {}
    assert polish_spoken("Of course.", st) == "Of course."          # a whole answer isn't stripped to nothing
    assert polish_spoken("Very good, ma'am.", {}, "ma'am") == "Very good, ma'am."


def test_a_long_first_sentence_starts_on_its_first_clause():
    from jarvis.voice.text import first_clause

    assert first_clause("Two meetings this afternoon, the first at half past two with the design team, and it's drizzling.") == \
        ["Two meetings this afternoon,", "the first at half past two with the design team, and it's drizzling."]
    assert first_clause("Done. The lights are off.") == ["Done. The lights are off."]
    long_sir = "Here is a rather long answer that keeps going for a good while, sir, so there is time to cut in."
    assert first_clause(long_sir) == ["Here is a rather long answer that keeps going for a good while, sir,",
                                      "so there is time to cut in."]    # not before "sir", but after it
