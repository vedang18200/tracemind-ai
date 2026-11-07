# mcp_servers/__init__.py stays empty
import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from urllib.parse import urljoin

from tracemind.config import settings
from tracemind.security.url_policy import validate_url

mcp = FastMCP("tracemind-web")

ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


@mcp.tool()
async def search_web(query: str, max_results: int = 5) -> dict:
    """Search the public web. Returns {"hits": [{"url", "title", "snippet"}]}."""
    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    if not query.strip() or len(query) > 500:
        raise ValueError("query must be 1-500 characters")
    max_results = max(1, min(max_results, 10))

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


if __name__ == "__main__":
    mcp.run()          # stdio transport