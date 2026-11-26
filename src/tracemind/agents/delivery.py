# agents/delivery.py
from langgraph.types import interrupt

from tracemind.agents.common import fail, node
from tracemind.approvals import (
    ApprovalError, build_send_report_action, create_approval,
    execute_send_report, get_approval,
)


def make_propose(factory):
    @node("propose_action")
    async def propose(state):
        if factory is None:
            return fail(state, "propose_action", "approvals_not_configured",
                        "delivery requested but approvals are not configured")
        try:
            action = build_send_report_action(
                task_id=state["task_id"],
                recipient=state["deliver_to"],
                subject="TraceMind report: " + " ".join(state["user_request"].split())[:100],
                report_md=state["report_md"] or "",
            )
        except ApprovalError as e:
            return fail(state, "propose_action", e.code, e.message)
        ap = await create_approval(factory, task_id=state["task_id"],
                                   requested_by=state["user_id"], action=action)
        return {"approval_id": ap.id, "status": "waiting_for_approval"}
    return propose


def make_gate(factory):
    @node("approval_gate")
    async def gate(state):
        # On resume this node re-runs from the top, so everything before interrupt() must be side-effect free.
        ap = await get_approval(factory, state["approval_id"] or "")
        if ap is None or ap.task_id != state["task_id"]:
            return fail(state, "approval_gate", "approval_missing", "no matching approval for this task")

        if ap.status == "pending":
            interrupt({"approval_id": ap.id, "action": ap.proposed_action})
            # Resumed. The resume payload is ignored; the DB is the only authority.
            ap = await get_approval(factory, ap.id)
            if ap is None or ap.status == "pending":
                return fail(state, "approval_gate", "resumed_while_pending",
                            "resumed before a decision was recorded; nothing was sent")

        if ap.status == "approved":
            return {"status": "running"}
        return {"status": "running", "delivery": "rejected"}
    return gate


def make_execute(factory):
    @node("execute_action")
    async def execute(state):
        ap = await get_approval(factory, state["approval_id"] or "")
        if ap is None or ap.task_id != state["task_id"]:
            return fail(state, "execute_action", "approval_missing", "no matching approval for this task")
        try:
            await execute_send_report(
                factory, approval_id=ap.id, action=ap.proposed_action, body=state["report_md"] or "",
            )
        except ApprovalError as e:
            return fail(state, "execute_action", e.code, e.message)
        return {"delivery": "sent"}
    return execute