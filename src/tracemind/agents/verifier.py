import json
import re

from tracemind.agents.common import load_prompt, node
from tracemind.llm import LLM
from tracemind.schemas import VerificationOutput, VerificationResult

NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def make_verifier(llm: LLM):
    system = load_prompt("v1_verifier")

    @node("verification")
    async def verifier(state):
        sources = {s.id: s for s in state["sources"]}
        findings = state["findings"]

        payload = json.dumps([
            {
                "id": f.id,
                "claim": f.claim,
                "excerpt": f.excerpt,
                "source_title": sources[f.source_id].title if f.source_id in sources else None,
            }
            for f in findings
        ], indent=2)

        out = await llm.structured(
            system=system,
            user=f"<findings>\n{payload}\n</findings>",
            schema=VerificationOutput,
        )

        valid_ids = {f.id for f in findings}
        by_id = {r.finding_id: r for r in out.results if r.finding_id in valid_ids}

        results: list[VerificationResult] = []
        for f in findings:
            r = by_id.get(f.id) or VerificationResult(
                finding_id=f.id, status="needs_review", reason="verifier returned no result"
            )
            if f.source_id not in sources:
                r = VerificationResult(finding_id=f.id, status="unsupported", reason="source missing")
            elif r.status == "verified":
                numbers = [n.rstrip(".,") for n in NUMBER.findall(f.claim)]
                missing = [n for n in numbers if n not in f.excerpt]
                if missing:
                    r = VerificationResult(
                        finding_id=f.id, status="unsupported",
                        reason=f"numbers {missing} not found in excerpt",
                    )
            results.append(r)

        claims = {f.id: f.claim for f in findings}
        focus = [
            f"find stronger evidence for: {claims[r.finding_id]}"
            for r in results if r.status != "verified"
        ]
        return {"verification": results, "research_focus": focus}

    return verifier