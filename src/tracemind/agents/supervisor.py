# agents/supervisor.py
from tracemind.agents.common import fail, load_prompt, node
from tracemind.llm import LLM
from tracemind.schemas import SupervisorPlan

def make_supervisor(llm: LLM):
    system = load_prompt("v1_supervisor")

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

        return {"plan": plan.steps}

    return supervisor