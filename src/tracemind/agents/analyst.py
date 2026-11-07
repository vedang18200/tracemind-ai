import json

from tracemind.agents.common import fail, load_prompt, node
from tracemind.llm import LLM
from tracemind.schemas import AnalysisResult


def make_analyst(llm: LLM):
    system = load_prompt("v2_analyst")   # use "v1_analyst" if you never created v2

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

        for p in result.platforms:
            if p.pricing is not None and not p.finding_ids:
                return fail(state, "analysis", "uncited_pricing", f"{p.name}: pricing without a citation")

        valid_ids = {f.id for f in state["findings"]}
        dropped: list[str] = []

        def clean(ids: list[str]) -> list[str]:
            dropped.extend(i for i in ids if i not in valid_ids)
            return [i for i in ids if i in valid_ids]

        platforms = []
        for p in result.platforms:
            ids = clean(p.finding_ids)
            if ids:                                   # a platform with no valid evidence is dropped
                platforms.append(p.model_copy(update={"finding_ids": ids}))

        gaps = []
        for g in result.market_gaps:
            ids = clean(g.finding_ids)
            if ids:                                   # a conclusion with no valid evidence is dropped
                gaps.append(g.model_copy(update={"finding_ids": ids}))

        if not platforms:
            return fail(state, "analysis", "invalid_citation",
                        f"No platform had valid citations. Unknown finding ids: {sorted(set(dropped))}")

        cleaned = result.model_copy(update={"platforms": platforms, "market_gaps": gaps})
        out = {"analysis": cleaned.model_dump()}
        if dropped:
            out["errors"] = [{"stage": "analysis", "code": "dropped_citation",
                              "message": f"removed unknown finding ids: {sorted(set(dropped))}"}]
        return out

    return analyst