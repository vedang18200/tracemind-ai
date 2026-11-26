import pytest

from tracemind.citations import CLOSE_THRESHOLD, check_quote
from tracemind.mcp_servers import web_server

PAGE = (
    "Pricing  Our Starter plan costs $29 per month and includes 50 interviews.\n\n"
    "The Pro plan — built for growing teams — costs $99 per month. "
    "Enterprise customers get SSO, audit logs and a dedicated success manager. "
    "“We cut hiring time in half,” said one customer."
)


def test_exact_match_ignores_whitespace_layout():
    r = check_quote(PAGE, "Starter plan costs $29 per   month")
    assert r["status"] == "exact_match" and r["similarity"] == 1.0
    assert "Starter plan costs $29" in r["context"]


def test_normalized_match_handles_case_dashes_and_curly_quotes():
    r = check_quote(PAGE, 'the pro plan - built for growing teams - costs $99')
    assert r["status"] == "normalized_match"
    r = check_quote(PAGE, '"We cut hiring time in half," said one customer')
    assert r["status"] == "normalized_match"


def test_context_comes_from_original_text_not_folded_text():
    r = check_quote(PAGE, "ENTERPRISE CUSTOMERS GET SSO")
    assert r["status"] == "normalized_match"
    assert "Enterprise customers get SSO" in r["context"]     # original casing preserved


def test_altered_number_is_close_match_not_a_match():
    # The classic hallucinated citation: right sentence, wrong figure.
    r = check_quote(PAGE, "Our Starter plan costs $19 per month and includes 50 interviews.")
    assert r["status"] == "close_match"
    assert CLOSE_THRESHOLD <= r["similarity"] < 1.0
    assert "$29" in r["closest_passage"]


def test_unrelated_quote_not_found():
    r = check_quote(PAGE, "The company was founded in 1998 in Berlin by three engineers.")
    assert r["status"] == "not_found"
    assert r["similarity"] < CLOSE_THRESHOLD


def test_empty_page():
    assert check_quote("", "anything at all here")["status"] == "not_found"


# ---------- the MCP tool, with the network fetch stubbed ----------

@pytest.fixture
def fake_page(monkeypatch):
    async def fake_fetch(url):
        return {"url": url, "title": "Pricing", "text": PAGE, "truncated": False}
    monkeypatch.setattr(web_server, "fetch_page", fake_fetch)


async def test_tool_reports_match_with_page_details(fake_page):
    r = await web_server.verify_citation("https://example.test/pricing", "Pro plan — built for growing teams")
    assert r["status"] == "exact_match"
    assert r["url"] == "https://example.test/pricing" and r["title"] == "Pricing"
    assert "note" not in r


@pytest.mark.parametrize("quote", ["short", "x" * 1001])
async def test_tool_rejects_bad_quote_length(fake_page, quote):
    with pytest.raises(ValueError):
        await web_server.verify_citation("https://example.test/", quote)


async def test_tool_warns_when_page_was_truncated(monkeypatch):
    async def truncated(url):
        return {"url": url, "title": "Long", "text": PAGE, "truncated": True}
    monkeypatch.setattr(web_server, "fetch_page", truncated)
    r = await web_server.verify_citation("https://example.test/", "a sentence that is not on the page at all")
    assert r["status"] == "not_found" and "truncated" in r["note"]


async def test_tool_inherits_ssrf_protection():
    from tracemind.security.url_policy import UrlNotAllowed
    with pytest.raises(UrlNotAllowed):
        await web_server.verify_citation("http://169.254.169.254/latest/meta-data/", "ami-id instance-id")
