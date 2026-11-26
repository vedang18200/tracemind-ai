from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import func, select

from tests.test_graph import base_responses
from tracemind.approvals import decide
from tracemind.db import Approval, OutboxMessage, User
from tracemind.graph import build_graph
from tracemind.llm import FakeLLM
from tracemind.schemas import Finding
from tracemind.state import new_state

CFG = {"configurable": {"thread_id": "t1"}}


@pytest.fixture
async def world(db):
    async with db() as s, s.begin():
        owner = User(email="owner@x.com", role="user")
        reviewer = User(email="rev@x.com", role="reviewer")
        s.add_all([owner, reviewer])
    return SimpleNamespace(db=db, owner=owner.id, reviewer=reviewer.id)


def graph_for(w, saver, **kw):
    return build_graph(FakeLLM(base_responses()), session_factory=w.db, checkpointer=saver, **kw)


async def start(w, saver, deliver_to="boss@example.com"):
    g = graph_for(w, saver)
    out = await g.ainvoke(new_state("t1", w.owner, "compare ai interview platforms", deliver_to=deliver_to), CFG)
    return g, out


async def count(w, model) -> int:
    async with w.db() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def the_approval(w) -> Approval:
    async with w.db() as s:
        return (await s.execute(select(Approval))).scalar_one()


async def test_pauses_at_gate_and_sends_nothing(world):
    saver = MemorySaver()
    g, _ = await start(world, saver)
    snap = await g.aget_state(CFG)
    assert snap.next == ("approval_gate",)
    assert snap.values["status"] == "waiting_for_approval"
    assert snap.values["approval_id"] == (await the_approval(world)).id
    assert await count(world, OutboxMessage) == 0


async def test_approve_then_resume_sends_exactly_once(world):
    saver = MemorySaver()
    _, _ = await start(world, saver)
    ap = await the_approval(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")

    g2 = graph_for(world, saver)                      # fresh graph object, same checkpoints
    out = await g2.ainvoke(Command(resume=True), CFG)
    assert out["status"] == "completed" and out["delivery"] == "sent"
    assert isinstance(out["findings"][0], Finding)    # state survived the checkpoint round trip
    assert await count(world, OutboxMessage) == 1

    try:                                              # a second resume may error or no-op; never a second email
        await g2.ainvoke(Command(resume=True), CFG)
    except Exception:
        pass
    assert await count(world, OutboxMessage) == 1


async def test_rejection_completes_without_sending(world):
    saver = MemorySaver()
    await start(world, saver)
    ap = await the_approval(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="rejected")
    out = await graph_for(world, saver).ainvoke(Command(resume=True), CFG)
    assert out["status"] == "completed" and out["delivery"] == "rejected"
    assert await count(world, OutboxMessage) == 0


async def test_resume_without_decision_fails_closed(world):
    saver = MemorySaver()
    await start(world, saver)
    out = await graph_for(world, saver).ainvoke(Command(resume=True), CFG)
    assert out["status"] == "failed"
    assert out["errors"][-1]["code"] == "resumed_while_pending"
    assert await count(world, OutboxMessage) == 0


async def test_report_tampered_while_paused_is_blocked(world):
    saver = MemorySaver()
    g, _ = await start(world, saver)
    await g.aupdate_state(CFG, {"report_md": "# Totally different report"})
    ap = await the_approval(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")
    out = await g.ainvoke(Command(resume=True), CFG)
    assert out["status"] == "failed"
    assert out["errors"][-1]["code"] == "hash_mismatch"
    assert await count(world, OutboxMessage) == 0


async def test_no_recipient_skips_approval_entirely(world):
    g = graph_for(world, MemorySaver())
    out = await g.ainvoke(new_state("t1", world.owner, "compare ai interview platforms"), CFG)
    assert out["status"] == "completed" and out["delivery"] == "not_requested"
    assert await count(world, Approval) == 0


async def test_invalid_recipient_fails_before_any_approval(world):
    g = graph_for(world, MemorySaver())
    out = await g.ainvoke(
        new_state("t1", world.owner, "compare ai interview platforms", deliver_to="not-an-email"), CFG)
    assert out["status"] == "failed" and out["errors"][-1]["code"] == "invalid_recipient"
    assert await count(world, Approval) == 0


async def test_delivery_without_approval_system_fails_closed():
    g = build_graph(FakeLLM(base_responses()), checkpointer=MemorySaver())
    out = await g.ainvoke(
        new_state("t1", "u", "compare ai interview platforms", deliver_to="boss@example.com"), CFG)
    assert out["status"] == "failed"
    assert out["errors"][-1]["code"] == "approvals_not_configured"