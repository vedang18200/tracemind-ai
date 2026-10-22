# agents/researcher.py
import hashlib
import re
from datetime import datetime, timezone

from tracemind.agents.common import fail, load_prompt, node
from tracemind.config import settings
from tracemind.llm import LLM
from tracemind.schemas import Finding, PageExtraction, Source
from tracemind.tools.stubs import fetch_page, search_web


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _source_id(url: str) -> str:
    return "src-" + hashlib.sha256(url.encode()).hexdigest()[:8]


def make_researcher(llm: LLM, search=search_web, fetch=fetch_page):
    system = load_prompt("v1_researcher")

    @node("research")
    async def researcher(state):
        known_urls = {s.url for s in state["sources"]}
        seen_claims = {_norm(f.claim) for f in state["findings"]}
        query = " ".join([state["user_request"], *state["research_focus"]])
        hits = await search(query)
        urls = [h.url for h in hits if h.url not in known_urls][: settings.max_sources]

        new_sources: list[Source] = []
        new_findings: list[Finding] = []
        errors: list[dict] = []

        for url in urls:
            try:
                page = await fetch(url)
            except Exception as e:  # tool failure: record and keep going
                errors.append({"stage": "research", "code": "fetch_failed", "message": f"{url}: {e}"})
                continue

            source = Source(
                id=_source_id(url), url=page.url, title=page.title,
                retrieved_at=datetime.now(timezone.utc), content_hash=page.content_hash,
            )
            new_sources.append(source)

            extraction = await llm.structured(
                system=system,
                user=f"Task: {state['user_request']}\n\n<page url=\"{page.url}\">\n{page.text}\n</page>",
                schema=PageExtraction,
            )

            page_text = _norm(page.text)
            for i, ef in enumerate(extraction.findings):
                if _norm(ef.excerpt) not in page_text:
                    errors.append({"stage": "research", "code": "ungrounded_finding",
                                   "message": f"{source.id}: excerpt not found in page"})
                    continue
                if _norm(ef.claim) in seen_claims:
                    continue
                seen_claims.add(_norm(ef.claim))
                new_findings.append(Finding(
                    id=f"f-{source.id}-{i}", claim=ef.claim, source_id=source.id,
                    excerpt=ef.excerpt, confidence=ef.confidence,
                ))

        if not new_findings and not state["findings"]:
            out = fail(state, "research", "empty_research", "No usable findings collected")
            out["errors"] = errors + out["errors"]
            out["sources"] = new_sources
            return out 
            
        return {
            "sources": new_sources,
            "findings": new_findings,
            "errors": errors,
            "research_rounds": state["research_rounds"] + 1,
        }

    return researcher