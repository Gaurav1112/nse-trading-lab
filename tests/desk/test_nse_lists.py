from datetime import date
import pytest
from nse_backtest.desk.nse_lists import parse_fno_ban, fetch_fno_ban

SAMPLE = "Securities in Ban For Trade Date 29-OCT-2026:\n1,ABCLTD\n2,XYZ-RE\n"


def test_parse():
    assert parse_fno_ban(SAMPLE) == frozenset({"ABCLTD", "XYZ-RE"})
    assert parse_fno_ban("Securities in Ban For Trade Date 29-OCT-2026: NIL\n") == frozenset()


def test_parse_rejects_unknown_format():
    with pytest.raises(ValueError):
        parse_fno_ban("<html>blocked</html>")


def test_fetch_ok_then_cached_then_stale(tmp_path):
    ok = fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=lambda u: SAMPLE)
    assert ok.status == "ok" and "ABCLTD" in ok.symbols

    def boom(u): raise OSError("down")
    assert fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=boom).status == "cached"
    stale = fetch_fno_ban(tmp_path, date(2026, 10, 30), fetcher=boom)
    assert stale.status == "stale" and "ABCLTD" in stale.symbols


def test_fetch_unavailable_never_raises(tmp_path):
    b = fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=lambda u: "<html/>")
    assert b.status == "unavailable" and b.symbols == frozenset()


@pytest.mark.parametrize("bad", [
    "Securities in Ban For Trade Date 29-OCT-2026:\n1,ABC,EQ\n",
    'Securities in Ban For Trade Date 29-OCT-2026:\n"1","ABC"\n',
    "Securities in Ban For Trade Date 29-OCT-2026:\n",
    "Securities in Ban For Trade Date 29-OCT-2026:\n1,ABC\nnotarow\n",
])
def test_parse_fails_loudly_on_changed_format(bad):
    with pytest.raises(ValueError):
        parse_fno_ban(bad)


def test_bad_row_message():
    with pytest.raises(ValueError, match="unexpected ban list row"):
        parse_fno_ban("Securities in Ban For Trade Date 29-OCT-2026:\n1,ABC,EQ\n")


def test_nil_lowercase_ok():
    assert parse_fno_ban("securities in ban ... nil\n") == frozenset()


def test_changed_format_fetch_never_raises(tmp_path):
    b = fetch_fno_ban(tmp_path, date(2026, 10, 29),
                      fetcher=lambda u: "Securities in Ban For Trade Date X:\n1,ABC,EQ\n")
    assert b.status == "unavailable"


def test_cache_write_failure_still_ok(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    b = fetch_fno_ban(f, date(2026, 10, 29), fetcher=lambda u: SAMPLE)
    assert b.status == "ok" and "ABCLTD" in b.symbols and "cache" in b.detail.lower()
