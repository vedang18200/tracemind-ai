import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from tracemind.approvals import (
    ApprovalError, build_send_report_action, canonical_hash,
    create_approval, decide, execute_send_report,
)
from tracemind.config import settings
from tracemind.db import AuditLog, OutboxMessage, User

REPORT = "# Report\n\nbody text"


def action(task="t1", recipient="boss@example.com", report=REPORT):
    return build_send_report_action(task_id=task, recipient=recipient,
                                    subject="Competitor report", report_md=report)


@pytest.fixture
async def world(db):
    async with db() as s, s.begin():
        owner = User(email="owner@x.com", role="user")
        reviewer = User(email="rev@x.com", role="reviewer")
        outsider = User(email="out@x.com", role="user")
        self_rev = User(email="selfrev@x.com", role="reviewer")
        s.add_all([owner, reviewer, outsider, self_rev])
    return SimpleNamespace(db=db, owner=owner.id, reviewer=reviewer.id,
                           outsider=outsider.id, self_rev=self_rev.id)


async def events(db) -> list[str]:
    async with db() as s:
        return list((await s.execute(select(AuditLog.event_type).order_by(AuditLog.id))).scalars())


async def outbox_count(db) -> int:
    async with db() as s:
        return (await s.execute(select(func.count()).select_from(OutboxMessage))).scalar_one()


async def pending(w, act=None):
    act = act or action()
    return act, await create_approval(w.db, task_id="t1", requested_by=w.owner, action=act)


# ---- pure logic -------------------------------------------------------------

def test_hash_is_stable_and_binding():
    a = action()
    assert canonical_hash(a) == canonical_hash(dict(reversed(list(a.items()))))
    assert canonical_hash(a) != canonical_hash(action(recipient="evil@example.com"))
    assert canonical_hash(a) != canonical_hash(action(report=REPORT + " edited"))
    assert canonical_hash(a) != canonical_hash(action(task="t2"))


@pytest.mark.parametrize("bad", [
    "not-an-email", "a@b", "", " ", "a b@example.com",
    "boss@example.com\nbcc:evil@x.com",
])
def test_bad_recipients_rejected(bad):
    with pytest.raises(ApprovalError) as e:
        action(recipient=bad)
    assert e.value.code == "invalid_recipient"


def test_domain_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "allowed_recipient_domains", ["corp.com"])
    action(recipient="ok@corp.com")
    with pytest.raises(ApprovalError):
        action(recipient="x@gmail.com")


# ---- approval lifecycle -----------------------------------------------------

async def test_create_approval_is_idempotent(world):
    act, a1 = await pending(world)
    _, a2 = await pending(world, act)
    assert a1.id == a2.id and a1.status == "pending"
    assert await events(world.db) == ["approval_requested"]


async def test_reviewer_can_approve(world):
    _, ap = await pending(world)
    out = await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer,
                       decision="approved", reason="looks right")
    assert out.status == "approved" and out.reviewed_by == world.reviewer
    assert "approval_approved" in await events(world.db)


async def test_non_reviewer_cannot_decide_and_attempt_is_audited(world):
    _, ap = await pending(world)
    with pytest.raises(ApprovalError) as e:
        await decide(world.db, approval_id=ap.id, reviewer_id=world.outsider, decision="approved")
    assert e.value.code == "unauthorized"
    assert "approval_decision_denied" in await events(world.db)      # survived the raise
    act = action()
    with pytest.raises(ApprovalError) as e2:                          # still pending, so can't execute
        await execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT)
    assert e2.value.code == "not_approved"


async def test_self_approval_blocked_by_default(world):
    act = action()
    ap = await create_approval(world.db, task_id="t1", requested_by=world.self_rev, action=act)
    with pytest.raises(ApprovalError) as e:
        await decide(world.db, approval_id=ap.id, reviewer_id=world.self_rev, decision="approved")
    assert e.value.code == "self_approval"


async def test_decision_replay_rejected(world):
    _, ap = await pending(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="rejected")
    with pytest.raises(ApprovalError) as e:      # flipping a rejection to approval must fail
        await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")
    assert e.value.code == "already_decided"
    assert "approval_replay_rejected" in await events(world.db)


async def test_unknown_approval_and_bad_decision(world):
    with pytest.raises(ApprovalError) as e:
        await decide(world.db, approval_id="nope", reviewer_id=world.reviewer, decision="approved")
    assert e.value.code == "not_found"
    with pytest.raises(ApprovalError) as e:
        await decide(world.db, approval_id="nope", reviewer_id=world.reviewer, decision="maybe")
    assert e.value.code == "invalid_decision"


# ---- protected execution ----------------------------------------------------

async def test_cannot_execute_before_approval(world):
    act, ap = await pending(world)
    with pytest.raises(ApprovalError) as e:
        await execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT)
    assert e.value.code == "not_approved"
    assert await outbox_count(world.db) == 0
    assert "execution_blocked" in await events(world.db)


async def test_rejected_action_never_executes(world):
    act, ap = await pending(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="rejected")
    with pytest.raises(ApprovalError) as e:
        await execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT)
    assert e.value.code == "not_approved"
    assert await outbox_count(world.db) == 0


async def test_approved_action_executes_once(world):
    act, ap = await pending(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")
    msg, created = await execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT)
    assert created and msg.recipient == "boss@example.com" and msg.body == REPORT
    msg2, created2 = await execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT)
    assert not created2 and msg2.id == msg.id
    assert await outbox_count(world.db) == 1
    ev = await events(world.db)
    assert "report_sent" in ev and "duplicate_send_suppressed" in ev


async def test_concurrent_resumes_send_exactly_one(world):
    act, ap = await pending(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")
    results = await asyncio.gather(*[
        execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT) for _ in range(5)
    ])
    assert sum(created for _, created in results) == 1
    assert await outbox_count(world.db) == 1


async def test_changed_recipient_needs_new_approval(world):
    act, ap = await pending(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")
    tampered = {**act, "recipient": "evil@example.com"}
    with pytest.raises(ApprovalError) as e:
        await execute_send_report(world.db, approval_id=ap.id, action=tampered, body=REPORT)
    assert e.value.code == "hash_mismatch"
    assert await outbox_count(world.db) == 0


async def test_edited_report_body_blocked(world):
    act, ap = await pending(world)
    await decide(world.db, approval_id=ap.id, reviewer_id=world.reviewer, decision="approved")
    with pytest.raises(ApprovalError) as e:
        await execute_send_report(world.db, approval_id=ap.id, action=act, body=REPORT + " (edited)")
    assert e.value.code == "hash_mismatch"
    assert await outbox_count(world.db) == 0