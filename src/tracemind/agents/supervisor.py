from tracemind.agents.common import fail, load_prompt, node
from tracemind.llm import LLM
from tracemind.schemas import SupervisorPlan


def make_supervisor(llm: LLM):
    system = load_prompt("v2_supervisor")

    @node("planning")
    async def supervisor(state):
        plan = await llm.structured(system=system, user=state["user_request"], schema=SupervisorPlan)

        # Validation happens in code; the model's output is never trusted as-is.
        if plan.task_type == "unsupported":
            return fail(state, "planning", "unsupported_task", "Request is not a supported workflow")
        if not plan.steps:
            return fail(state, "planning", "empty_plan", "Supervisor returned no steps")
        ids = [s.id for s in plan.steps]
        if len(ids) != len(set(ids)):
            return fail(state, "planning", "duplicate_step_ids", "Plan step ids must be unique")

        queries = [q.strip() for q in plan.search_queries if q.strip()]
        if not queries or len(queries) > 5 or any(len(q) > 200 for q in queries):
            return fail(state, "planning", "invalid_queries",
                        "Need 1-5 search queries, each under 200 chars")

        return {"plan": plan.steps, "search_queries": queries}

    return supervisor