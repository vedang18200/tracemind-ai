import argparse
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from langgraph.types import Command
from sqlalchemy import select

from tracemind.approvals import ApprovalError, decide
from tracemind.checkpoint import open_checkpointer
from tracemind.db import (
    Approval, OutboxMessage, get_or_create_user, init_db, make_engine, make_sessionmaker,
)
from tracemind.graph import build_graph
from tracemind.llm import FakeLLM, get_llm
from tracemind.mcp_client import connect_stdio
from tracemind.state import new_state
from tracemind.tools.mcp_tools import researcher_tools

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)

OWNER, REVIEWER = "owner@tracemind.local", "reviewer@tracemind.local"
REQUEST = ("Research the top five AI interview platforms, compare their pricing and features, "
           "identify market gaps, and generate a competitor analysis report with sources.")


@asynccontextmanager
async def runtime(live: bool):
    engine = make_engine()
    await init_db(engine)
    factory = make_sessionmaker(engine)
    try:
        async with open_checkpointer() as saver:
            if live:
                async with connect_stdio() as client:
                    search, fetch = researcher_tools(client)
                    yield build_graph(get_llm(), strong_llm=get_llm(strong=True), search=search,
                                      fetch=fetch, session_factory=factory, checkpointer=saver), factory
            else:       # reading state or deciding never calls a model
                yield build_graph(FakeLLM({}), session_factory=factory, checkpointer=saver), factory
    finally:
        await engine.dispose()


def cfg(task_id: str) -> dict:
    return {"configurable": {"thread_id": task_id}}


async def describe(graph, task_id: str) -> None:
    snap = await graph.aget_state(cfg(task_id))
    v = snap.values
    if not v:
        print(f"no run found for task {task_id}")
        return
    print(f"task {task_id}: status={v['status']} stage={v['current_stage']} "
          f"next={list(snap.next)} delivery={v['delivery']} approval={v['approval_id']}")
    for e in v["errors"][-3:]:
        print(f"  error: {e['code']}: {e['message'][:120]}")


async def cmd_seed(a):
    async with runtime(False) as (_, f):
        await get_or_create_user(f, OWNER, "user")
        await get_or_create_user(f, REVIEWER, "reviewer")
    print(f"users ready: {OWNER} (user), {REVIEWER} (reviewer)")


async def cmd_start(a):
    task_id = uuid.uuid4().hex[:8]
    print(f"task_id: {task_id}", flush=True)         # printed first so you have it even if you kill the run
    async with runtime(True) as (graph, f):
        owner = await get_or_create_user(f, OWNER, "user")
        await graph.ainvoke(new_state(task_id, owner, a.request, deliver_to=a.to), cfg(task_id))
        await describe(graph, task_id)


async def cmd_status(a):
    async with runtime(False) as (graph, _):
        await describe(graph, a.task_id)


async def cmd_pending(a):
    async with runtime(False) as (_, f):
        async with f() as s:
            rows = (await s.execute(select(Approval).where(Approval.status == "pending"))).scalars().all()
    for r in rows:
        act = r.proposed_action
        print(f"{r.id}  task={r.task_id}  to={act['recipient']}  subject={act['subject']!r}  "
              f"hash={r.action_hash[:12]}")
    if not rows:
        print("no pending approvals")


async def cmd_decide(a):
    async with runtime(False) as (_, f):
        reviewer = await get_or_create_user(f, REVIEWER, "reviewer")
        try:
            ap = await decide(f, approval_id=a.approval_id, reviewer_id=reviewer,
                              decision="approved" if a.decision == "approve" else "rejected",
                              reason="demo")
            print(f"recorded: {ap.status}")
        except ApprovalError as e:
            print(f"refused: {e.code} ({e.message})")


async def cmd_resume(a):
    async with runtime(True) as (graph, _):
        snap = await graph.aget_state(cfg(a.task_id))
        if not snap.values:
            print("no run found")
            return
        if not snap.next:
            print("already finished; nothing to resume")
            await describe(graph, a.task_id)
            return
        interrupted = any(t.interrupts for t in snap.tasks)
        await graph.ainvoke(Command(resume=True) if interrupted else None, cfg(a.task_id))
        await describe(graph, a.task_id)


async def cmd_outbox(a):
    async with runtime(False) as (_, f):
        async with f() as s:
            rows = (await s.execute(select(OutboxMessage))).scalars().all()
    print(f"{len(rows)} message(s) in outbox")
    for r in rows:
        print(f"  to={r.recipient}  subject={r.subject!r}  body_chars={len(r.body)}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    s = sub.add_parser("start")
    s.add_argument("--to", default="boss@example.com")
    s.add_argument("request", nargs="?", default=REQUEST)
    for name in ("status", "resume"):
        sub.add_parser(name).add_argument("task_id")
    sub.add_parser("pending")
    d = sub.add_parser("decide")
    d.add_argument("approval_id")
    d.add_argument("decision", choices=["approve", "reject"])
    sub.add_parser("outbox")
    a = p.parse_args()
    asyncio.run({"seed": cmd_seed, "start": cmd_start, "status": cmd_status, "pending": cmd_pending,
                 "decide": cmd_decide, "resume": cmd_resume, "outbox": cmd_outbox}[a.cmd](a))


main()