import json

from tracemind.agents.common import fail, load_prompt, node
from tracemind.llm import LLM
from tracemind.schemas import AnalysisResult


def make_analyst(llm: LLM):
    system = load_prompt("v1_analyst")   
    @node("analysis")
    async def analyst(state):
        sources = {s.id: s for s in state["sources"]}
        payload = json.dumps([
            {
                "id": f.id,
                "claim": f.claim,
                "excerpt": f.excerpt,
                "source_title": sources[f.source_id].title if f.source_id in sources else None,
                "source_url": sources[f.source_id].url if f.source_id in sources else None,
            }
            for f in state["findings"]
        ], indent=2)

        result = await llm.structured(
            system=system,
            user=f"Task: {state['user_request']}\n\n<findings>\n{payload}\n</findings>",
            schema=AnalysisResult,
        )

        valid_ids = {f.id for f in state["findings"]}
        cited = [i for p in result.platforms for i in p.finding_ids]
        cited += [i for g in result.market_gaps for i in g.finding_ids]
        unknown = sorted({i for i in cited if i not in valid_ids})
        if unknown:
            return fail(state, "analysis", "invalid_citation", f"Unknown finding ids: {unknown}")

        for p in result.platforms:
            if p.pricing is not None and not p.finding_ids:
                return fail(state, "analysis", "uncited_pricing", f"{p.name}: pricing without a citation")

        return {"analysis": result.model_dump()}

    return analyst