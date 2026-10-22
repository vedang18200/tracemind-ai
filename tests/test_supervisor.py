import pytest
from tracemind.agents.supervisor import make_supervisor
from tracemind.llm import FakeLLM
from tracemind.schemas import PlanStep, SupervisorPlan
from tracemind.state import new_state

def steps():
    return [PlanStep(id=f"s{i}", description=a, agent=a)
            for i, a in enumerate(["researcher", "analyst", "verifier", "reporter"], 1)]

async def run(plan, state=None):
    llm = FakeLLM({"SupervisorPlan": plan})
    return await make_supervisor(llm)(state or new_state("t", "u", "compare platforms"))

async def test_valid_plan():
    out = await run(SupervisorPlan(task_type="competitor_analysis", steps=steps()))
    assert len(out["plan"]) == 4
    assert out["step_count"] == 1
    assert out["current_stage"] == "planning"

async def test_unsupported_task_fails():
    out = await run(SupervisorPlan(task_type="unsupported", steps=[]))
    assert out["status"] == "failed"
    assert out["errors"][0]["code"] == "unsupported_task"

async def test_empty_plan_fails():
    out = await run(SupervisorPlan(task_type="competitor_analysis", steps=[]))
    assert out["errors"][0]["code"] == "empty_plan"

async def test_duplicate_ids_fail():
    s = steps(); s[1].id = "s1"
    out = await run(SupervisorPlan(task_type="competitor_analysis", steps=s))
    assert out["errors"][0]["code"] == "duplicate_step_ids"

async def test_step_limit_enforced():
    state = new_state("t", "u", "x")
    state["step_count"] = 999
    out = await run(SupervisorPlan(task_type="competitor_analysis", steps=steps()), state)
    assert out["errors"][0]["code"] == "step_limit"