# mcp_servers/__init__.py stays empty
import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from datetime import datetime, timezone
from urllib.parse import urljoin

from tracemind.citations import check_quote
from tracemind.config import settings
from tracemind.security.url_policy import pinned_transport, validate_url

mcp = FastMCP("tracemind-web")

ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}

# Per-process daily budget for the paid/free-tier search API. Resets on restart, so it is a
# safety net for a public demo, not exact accounting.
_search_budget = {"day": "", "used": 0}


def _take_search_budget() -> None:
    if settings.search_daily_limit <= 0:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    if _search_budget["day"] != today:
        _search_budget.update(day=today, used=0)
    if _search_budget["used"] >= settings.search_daily_limit:
        raise RuntimeError("daily search limit reached for this demo server; try again tomorrow")
    _search_budget["used"] += 1


@mcp.tool()
async def search_web(query: str, max_results: int = 5) -> dict:
    """Search the public web. Returns {"hits": [{"url", "title", "snippet"}]}."""
    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    if not query.strip() or len(query) > 500:
        raise ValueError("query must be 1-500 characters")
    max_results = max(1, min(max_results, 10))
    _take_search_budget()

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={"query": query, "max_results": max_results},
        )
        r.raise_for_status()

    hits = [
        {"url": x["url"], "title": x.get("title", ""), "snippet": (x.get("content") or "")[:200]}
        for x in r.json().get("results", [])
    ]
    return {"hits": hits}


@mcp.tool()
async def fetch_page(url: str) -> dict:
    """Fetch a public web page. Returns {"url", "title", "text", "truncated"}."""
    current = url
    async with httpx.AsyncClient(
        transport=pinned_transport(),     # re-validates the IP at connect time (DNS rebinding)
        trust_env=False,                  # no env proxies: they would bypass the pinned transport
        follow_redirects=False,
        timeout=settings.fetch_timeout_seconds,
        headers={"User-Agent": "TraceMind/0.1 (research bot)"},
    ) as client:
        for _ in range(settings.fetch_max_redirects + 1):
            await validate_url(current)
            async with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("redirect without location")
                    current = urljoin(current, location)
                    continue

                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_CONTENT_TYPES:
                    raise ValueError(f"content type not allowed: {ctype or 'unknown'}")

                body = bytearray()
                truncated = False
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > settings.fetch_max_bytes:
                        truncated = True
                        break
                encoding = resp.encoding or "utf-8"
                break
        else:
            raise ValueError("too many redirects")

    html = bytes(body).decode(encoding, errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else current
    text = soup.get_text(" ", strip=True)
    if len(text) > settings.fetch_max_chars:
        text, truncated = text[: settings.fetch_max_chars], True

    return {"url": current, "title": title, "text": text, "truncated": truncated}


@mcp.tool()
async def verify_citation(url: str, quote: str) -> dict:
    """Check whether a quoted passage really appears on a web page, so an AI can fact-check
    its own citations. Fetches the page (same SSRF protections as fetch_page) and returns
    {"url", "title", "status", "similarity", "context", ...} where status is one of:
    exact_match, normalized_match (only case/quote-style/dash differences),
    close_match (similar but altered or paraphrased; see closest_passage), not_found."""
    quote = quote.strip()
    if not 10 <= len(quote) <= 1000:
        raise ValueError("quote must be 10-1000 characters")

    page = await fetch_page(url)
    result = {"url": page["url"], "title": page["title"], "quote": quote,
              **check_quote(page["text"], quote)}
    if result["status"] in ("close_match", "not_found") and page["truncated"]:
        result["note"] = (f"page text was truncated to {settings.fetch_max_chars} characters; "
                          "the quote may appear further down the page")
    return result


if __name__ == "__main__":
    mcp.run()          # stdio transport