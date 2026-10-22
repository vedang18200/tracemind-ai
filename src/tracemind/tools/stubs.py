# tools/stubs.py
import hashlib
from pydantic import BaseModel

class SearchHit(BaseModel):
    url: str
    title: str
    snippet: str

class Page(BaseModel):
    url: str
    title: str
    text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

_PAGES = {
    "https://example.test/acme": Page(
        url="https://example.test/acme", title="AcmeInterview Pricing",
        text="AcmeInterview offers AI video interviews. Pro plan: $99/month. Features: async video, scoring.",
    ),
    "https://example.test/beta": Page(
        url="https://example.test/beta", title="BetaHire Features",
        text="BetaHire provides live AI interview assistance. Pricing on request. Features: transcripts, ATS sync.",
    ),
    "https://example.test/gamma": Page(
        url="https://example.test/gamma", title="GammaScreen",
        text="GammaScreen: $149/month. Features: coding interviews, proctoring.",
    ),
}

async def search_web(query: str) -> list[SearchHit]:
    return [SearchHit(url=p.url, title=p.title, snippet=p.text[:80]) for p in _PAGES.values()]

async def fetch_page(url: str) -> Page:
    if url not in _PAGES:
        raise LookupError(f"page not found: {url}")
    return _PAGES[url]