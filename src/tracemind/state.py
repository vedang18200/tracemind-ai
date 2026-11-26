"""State for the TraceMind application."""
import operator
from typing import Annotated, Literal, TypedDict

from tracemind.schemas import Finding, PlanStep, Source, VerificationResult

SCHEMA_VERSION = 1

class TraceMindState(TypedDict):
    schema_version: int          
    task_id: str
    user_id: str
    user_request: str
    plan: list[PlanStep]
    findings: Annotated[list[Finding], operator.add]
    sources: Annotated[list[Source], operator.add]
    analysis: dict | None
    verification: list[VerificationResult]   # no reducer on purpose: verifier overwrites each round
    report_md: str | None
    approval_id: str | None
    status: Literal["running", "waiting_for_approval", "completed", "failed", "cancelled"]
    current_stage: str
    step_count: int
    research_rounds: int
    errors: Annotated[list[dict], operator.add]
    research_focus: list[str] 
    search_queries: list[str]
    deliver_to: str | None
    delivery: Literal["not_requested", "sent", "rejected"]

def new_state(task_id: str, user_id: str, request: str, deliver_to: str | None = None) -> TraceMindState:
    return TraceMindState(
        schema_version=SCHEMA_VERSION, task_id=task_id, user_id=user_id,
        user_request=request, plan=[], findings=[], sources=[], analysis=None,
        verification=[], report_md=None, approval_id=None, status="running",
        current_stage="initialization", step_count=0, research_rounds=0, errors=[],
        research_focus=[], search_queries=[], deliver_to=deliver_to, delivery="not_requested",
    )