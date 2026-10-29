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


# ---- Final fix wave B2: bounded read, https + NSE host only, mkstemp cache writes ----
class _Resp:
    def __init__(self, body: bytes, url: str):
        self.body, self.url, self.asked = body, url, None

    def read(self, n=-1):
        self.asked = n
        return self.body if n is None or n < 0 else self.body[:n]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, resp):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: resp)


def test_b2_default_fetcher_bounds_the_read(monkeypatch):
    from nse_backtest.desk import nse_lists
    r = _Resp(SAMPLE.encode(), "https://nsearchives.nseindia.com/content/fo/fo_secban.csv")
    _patch_urlopen(monkeypatch, r)
    assert nse_lists._default_fetcher(nse_lists.FNO_BAN_URL) == SAMPLE and r.asked == 1_000_001  # reads MAX+1 to detect truncation


@pytest.mark.parametrize("final", ["http://nsearchives.nseindia.com/content/fo/fo_secban.csv",
                                   "https://evil.example.com/fo_secban.csv",
                                   "https://nsearchives.nseindia.com.evil.example/x"])
def test_b2_redirect_off_nse_https_is_rejected_and_falls_back_to_cache(monkeypatch, tmp_path, final):
    from nse_backtest.desk import nse_lists
    fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=lambda u: SAMPLE)  # seed the cache
    _patch_urlopen(monkeypatch, _Resp(SAMPLE.encode(), final))
    with pytest.raises(ValueError):
        nse_lists._default_fetcher(nse_lists.FNO_BAN_URL)
    b = fetch_fno_ban(tmp_path, date(2026, 10, 29))
    assert b.status == "cached" and "ABCLTD" in b.symbols


def test_b2_cache_write_uses_unique_temp_and_leaves_none(tmp_path, monkeypatch):
    import tempfile
    from nse_backtest.desk import nse_lists
    made = []
    real = tempfile.mkstemp
    monkeypatch.setattr(nse_lists.tempfile, "mkstemp", lambda **k: made.append(k) or real(**k))
    fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=lambda u: SAMPLE)
    assert len(made) == 2 and all(k["dir"] == tmp_path for k in made)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fo_secban.csv", "fo_secban.date"]


# ---- Last round: bounded read raises on truncation ----
def test_lr_default_fetcher_raises_when_body_exceeds_cap(monkeypatch):
    import urllib.request
    from nse_backtest.desk import nse_lists

    class R:
        def __init__(self, n): self.n = n
        def geturl(self): return nse_lists.FNO_BAN_URL
        def read(self, k=-1): return b"x" * (self.n if k < 0 else min(k, self.n))
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: R(nse_lists._MAX_BYTES + 500))
    with pytest.raises(ValueError, match="too large"):
        nse_lists._default_fetcher(nse_lists.FNO_BAN_URL)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: R(nse_lists._MAX_BYTES))
    assert len(nse_lists._default_fetcher(nse_lists.FNO_BAN_URL)) == nse_lists._MAX_BYTES
