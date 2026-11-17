"""Reading Thunderbird's .msf indexes (Mork). Edge cases follow the ones Thunderbird's own parser is
tested against: brackets and slashes inside values, values split across lines, rows straight after
a table, padded cells, escapes, hex-encoded UTF-8, and later transactions updating rows."""
from jarvis.mac.mork import Mork

HEAD = "< <(a=c)>(80=ns:msg:db:row:scope:msgs:all)(81=subject)(83=message-id)(88=flags)>\n"


def rows(text):
    return {r["@id"]: r for r in Mork.parse(text).rows and [
        {"@id": k.split(":")[0], **v} for k, v in Mork.parse(text).rows.items()]}


def test_angle_bracket_in_a_value_and_a_line_break_before_the_equals():
    r = rows(HEAD + "<(8A=Test -> Inbox)(DB\n =Test POP3)>\n{1:^80 {(k^96:c)(s=9)}\n  [1(^81^8A)]\n  [2(^81^DB)]\n}")
    assert r["1"]["subject"] == "Test -> Inbox" and r["2"]["subject"] == "Test POP3"


def test_double_slashes_in_values_are_not_comments():
    r = rows(HEAD + "<(8A=https://bugzilla.mozilla.org)(8B=Meeting // Updates)>\n{1:^80 [1(^81^8A)] [2(^81^8B)]}")
    assert r["1"]["subject"] == "https://bugzilla.mozilla.org" and r["2"]["subject"] == "Meeting // Updates"


def test_brackets_in_values_dont_make_phantom_rows():
    r = rows(HEAD + "<(8A=[127.0.0.1])(8B=Received from [unix socket] by server)>\n{1:^80 [1(^81^8A)] [2(^81^8B)]}")
    assert set(r) == {"1", "2"} and r["1"]["subject"] == "[127.0.0.1]"


def test_a_row_right_after_a_table():
    r = rows(HEAD + "<(8A=Adjacent Row Test)>\n{7:^80 {(k^97:c)(s=9)7:m } 7 8 9 }[A:^80(^81^8A)]")
    assert r["A"]["subject"] == "Adjacent Row Test"


def test_padded_cells_keep_inner_spaces():
    r = rows(HEAD + "  {1:^80 {(k^96:c)(s=9)}\n    [ 1( ^81=Hello World ) ]\n  }")
    assert r["1"]["subject"] == "Hello World"


def test_escapes_hex_utf8_and_continuations():
    text = HEAD + "<(8A=Zo$C3$AB says (hi\\) \\\\ bye \\$5 off)(8B=long \\\nsubject)>\n{1:^80 [1(^81^8A)] [2(^81^8B)]}"
    r = rows(text)
    assert r["1"]["subject"] == "Zoë says (hi) \\ bye $5 off" and r["2"]["subject"] == "long subject"


def test_later_transactions_update_and_clear_rows():
    text = (HEAD + "<(A0=hello@x)(A1=Hello)>\n{1:^80 {(k^96:c)(s=9)} [1(^83^A0)(^81^A1)(^88=0)]}\n"
            "@$${2{@[1:^80(^88=1)]@$$}2}@\n"                 # marked read later
            "@$${3{@[-2:^80(^83=other@x)(^88=4)]@$$}3}@\n"     # a new row, cleared then set
            "@$${4{@[1:^80(^88=5)")                           # a half-written transaction at the end
    m = Mork.parse(text)
    msgs = {r["message-id"]: r for r in m.messages()}
    assert msgs["hello@x"]["flags"] == "1" and msgs["hello@x"]["subject"] == "Hello"
    assert msgs["other@x"]["flags"] == "4"


def test_comments_in_the_column_dictionary():
    text = ("// <!-- <mdb:mork:z v=\"1.4\"/> -->\n< <(a=c)> // (f=iso-8859-1)\n  (80=ns:msg:db:row:scope:msgs:all)"
            "(81=subject)(83=message-id)>\n<(90=Hi)(91=a@b)>\n{1:^80 [1(^81^90)(^83^91)]}")
    assert Mork.parse(text).messages() == [{"@id": "1", "subject": "Hi", "message-id": "a@b"}]
