import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracemind.config import settings
from tracemind.db import Approval, AuditLog, OutboxMessage, User

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
REVIEWER_ROLES = {"reviewer", "admin"}


class ApprovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def canonical_hash(action: dict) -> str:
    blob = json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _sha256(blob)


def build_send_report_action(*, task_id: str, recipient: str, subject: str, report_md: str) -> dict:
    """Trusted layer: validates the proposal and pins it to the exact report content."""
    r = recipient.strip().lower()
    if len(r) > 254 or not EMAIL.fullmatch(r):
        raise ApprovalError("invalid_recipient", "recipient is not a valid email address")
    allowed = [d.lower() for d in settings.allowed_recipient_domains]
    if allowed and r.rsplit("@", 1)[1] not in allowed:
        raise ApprovalError("invalid_recipient", "recipient domain is not allowed")
    subject = subject.strip()
    if not subject or len(subject) > 200 or "\n" in subject or "\r" in subject:
        raise ApprovalError("invalid_subject", "subject must be 1-200 characters on one line")
    return {
        "type": "send_report",
        "task_id": task_id,
        "recipient": r,
        "subject": subject,
        "report_sha256": _sha256(report_md),
    }


def _audit(session, *, event: str, actor_id=None, task_id=None, resource_id=None, **meta) -> None:
    session.add(AuditLog(actor_id=actor_id, task_id=task_id, event_type=event,
                         resource_id=resource_id, meta=meta))


async def get_approval(factory, approval_id: str) -> Approval | None:
    async with factory() as session:
        return await session.get(Approval, approval_id)

async def create_approval(factory, *, task_id: str, requested_by: str, action: dict) -> Approval:
    h = canonical_hash(action)
    async with factory() as session, session.begin():
        existing = (await session.execute(
            select(Approval).where(Approval.task_id == task_id, Approval.action_hash == h)
        )).scalar_one_or_none()
        if existing:
            return existing
        a = Approval(task_id=task_id, proposed_action=action, action_hash=h, requested_by=requested_by)
        session.add(a)
        await session.flush()
        _audit(session, event="approval_requested", actor_id=requested_by,
               task_id=task_id, resource_id=a.id, action_hash=h)
        return a


async def decide(factory, *, approval_id: str, reviewer_id: str, decision: str, reason: str = "") -> Approval:
    if decision not in ("approved", "rejected"):
        raise ApprovalError("invalid_decision", "decision must be 'approved' or 'rejected'")

    error: ApprovalError | None = None
    result: Approval | None = None
    async with factory() as session, session.begin():
        a = (await session.execute(
            select(Approval).where(Approval.id == approval_id).with_for_update()
        )).scalar_one_or_none()

        if a is None:
            error = ApprovalError("not_found", "approval not found")
        else:
            reviewer = await session.get(User, reviewer_id)
            if reviewer is None or reviewer.role not in REVIEWER_ROLES:
                _audit(session, event="approval_decision_denied", actor_id=reviewer_id,
                       task_id=a.task_id, resource_id=a.id, reason="not_a_reviewer")
                error = ApprovalError("unauthorized", "user is not allowed to decide approvals")
            elif reviewer_id == a.requested_by and not settings.allow_self_approval:
                _audit(session, event="approval_decision_denied", actor_id=reviewer_id,
                       task_id=a.task_id, resource_id=a.id, reason="self_approval")
                error = ApprovalError("self_approval", "requesters cannot approve their own actions")
            elif a.status != "pending":
                _audit(session, event="approval_replay_rejected", actor_id=reviewer_id,
                       task_id=a.task_id, resource_id=a.id, status=a.status, attempted=decision)
                error = ApprovalError("already_decided", f"approval is already {a.status}")
            else:
                a.status = decision
                a.reviewed_by = reviewer_id
                a.reviewed_at = datetime.now(timezone.utc)
                a.decision_reason = reason[:1000]
                _audit(session, event=f"approval_{decision}", actor_id=reviewer_id,
                       task_id=a.task_id, resource_id=a.id, action_hash=a.action_hash)
                result = a

    if error:
        raise error
    return result


async def execute_send_report(factory, *, approval_id: str, action: dict, body: str):
    """Sends only if the DB says approved AND the action and body match what was approved.
    Returns (outbox_row, created). created is False when this was a duplicate."""
    h = canonical_hash(action)
    error: ApprovalError | None = None
    msg: OutboxMessage | None = None
    created = False

    async with factory() as session, session.begin():
        a = await session.get(Approval, approval_id)
        if a is None:
            error = ApprovalError("not_found", "approval not found")
        elif a.status != "approved":
            _audit(session, event="execution_blocked", task_id=a.task_id, resource_id=a.id,
                   reason=f"status={a.status}")
            error = ApprovalError("not_approved", f"approval is {a.status}")
        elif a.action_hash != h or _sha256(body) != action.get("report_sha256"):
            _audit(session, event="execution_blocked", task_id=a.task_id, resource_id=a.id,
                   reason="hash_mismatch")
            error = ApprovalError("hash_mismatch", "action or body differs from what was approved")
        else:
            inserted = (await session.execute(
                pg_insert(OutboxMessage)
                .values(idempotency_key=h, approval_id=a.id, recipient=action["recipient"],
                        subject=action["subject"], body=body)
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(OutboxMessage.id)
            )).scalar_one_or_none()
            created = inserted is not None
            msg = (await session.execute(
                select(OutboxMessage).where(OutboxMessage.idempotency_key == h)
            )).scalar_one()
            _audit(session,
                   event="report_sent" if created else "duplicate_send_suppressed",
                   task_id=a.task_id, resource_id=a.id, recipient=action["recipient"])

    if error:
        raise error
    return msg, created