"""F1 D2: one helper for any fetched or exception text shown on the page."""
from pathlib import Path

import pytest

from nse_backtest.desk import textsafe
from nse_backtest.desk.textsafe import md_escape, safe_url, scrub


@pytest.fixture
def home(monkeypatch):
    h = "/Users/Jane Q Doe"  # a home directory with spaces
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(h)))
    return h


def test_scrub_redacts_the_home_dir_even_with_spaces(home):
    out = scrub(f"cannot open {home}/.nse-trading-lab/desk_audit.jsonl: permission denied")
    assert "Jane" not in out and "Doe" not in out and "/Users" not in out
    assert "desk_audit.jsonl" in out and "permission denied" in out


def test_scrub_strips_file_urls_and_paths_but_keeps_http_urls(home):
    assert scrub("see file:///Users/x/secret/notes.txt now") == "see notes.txt now"
    assert scrub("cannot open error:/var/tmp/secret/x.py") == "cannot open error:x.py"
    assert scrub("no file /var/tmp/abc/yahoo-ACME.xml") == "no file yahoo-ACME.xml"
    assert scrub("HTTP 404 for https://data.sec.gov/api/x/CIK1.json") == \
        "HTTP 404 for https://data.sec.gov/api/x/CIK1.json"
    assert scrub(r"C:\Users\x\a.txt failed") == "a.txt failed"


def test_scrub_is_bounded_and_takes_anything():
    assert len(scrub("x" * 5000)) <= 300
    assert scrub(OSError(2, "No such file", "/srv/x/y.json")).endswith("y.json'")
    assert scrub(None) == "None"


def test_md_escape_escapes_markdown_dollar_and_colon():
    assert md_escape("$5B: [x](y) *b*") == r"\$5B\: \[x\]\(y\) \*b\*"
    assert md_escape("a_b") == r"a\_b"


@pytest.mark.parametrize("bad", ["https://x.test/a\nb", "https://x.test/a b", 'https://x.test/"x', "https://x.test/<x>",
                                 "https://x.test/`x`", "https://x.test/\x7f\x00", "javascript:alert(1)",
                                 "file:///etc/passwd", "ftp://x.test/a", None, 3])
def test_safe_url_rejects(bad):
    assert safe_url(bad) is None


def test_safe_url_percent_quotes_the_rest():
    assert safe_url("https://x.test/a(b)c") == "https://x.test/a%28b%29c"
    assert safe_url("http://x.test/q?a=1&b=%20") == "http://x.test/q?a=1&b=%20"
    assert safe_url("https://x.test/é") == "https://x.test/%C3%A9"


def test_the_page_and_card_use_the_shared_helpers():
    from components import desk_card
    assert desk_card.md_escape is textsafe.md_escape and desk_card.safe_url is textsafe.safe_url
    from nse_backtest.desk import research
    assert not hasattr(research, "_scrub")
