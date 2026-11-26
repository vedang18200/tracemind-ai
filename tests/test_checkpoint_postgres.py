import pytest
from langgraph.types import Command

from tests.test_approval_flow import CFG, count, graph_for, the_approval, world  # noqa: F401
from tests.test_graph import base_responses
from tracemind.approvals import decide
from tracemind.checkpoint import open_checkpointer
from tracemind.db import OutboxMessage
from tracemind.graph import build_graph
from tracemind.llm import FakeLLM
from tracemind.schemas import Finding
from tracemind.state import new_state

REQUEST = "compare ai interview platforms"


async def test_pause_survives_losing_the_connection(world, schema_dsn):
    async with open_checkpointer(schema_dsn) as saver:
        await graph_for(world, saver).ainvoke(
            new_state("t1", world.owner, REQUEST, deliver_to="boss@example.com"), CFG)
    # first connection is closed: nothing in memory knows about this run any more

    ap = await the_approval(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")

    async with open_checkpointer(schema_dsn) as saver2:
        g2 = graph_for(world, saver2)
        assert (await g2.aget_state(CFG)).next == ("approval_gate",)
        out = await g2.ainvoke(Command(resume=True), CFG)

    assert out["status"] == "completed" and out["delivery"] == "sent"
    assert isinstance(out["findings"][0], Finding)        # Pydantic objects survive Postgres
    assert await count(world, OutboxMessage) == 1


class ProcessDied(BaseException):
    """Not an Exception, so nothing in the graph catches it. Simulates kill -9."""


async def test_crash_mid_run_resumes_without_redoing_finished_work(world, schema_dsn):
    async def dying_search(query):
        raise ProcessDied()

    async with open_checkpointer(schema_dsn) as saver:
        g = build_graph(FakeLLM(base_responses()), search=dying_search,
                        session_factory=world.db, checkpointer=saver)
        with pytest.raises(ProcessDied):
            await g.ainvoke(new_state("t1", world.owner, REQUEST), CFG)

    llm2 = FakeLLM(base_responses())
    async with open_checkpointer(schema_dsn) as saver2:
        g2 = build_graph(llm2, session_factory=world.db, checkpointer=saver2)
        assert (await g2.aget_state(CFG)).next == ("researcher",)
        out = await g2.ainvoke(None, CFG)                  # None = continue from the last checkpoint

    assert out["status"] == "completed"
    assert not any(c["schema"] == "SupervisorPlan" for c in llm2.calls)    # planning was not repeated