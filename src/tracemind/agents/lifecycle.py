import re

from tracemind.agents.common import fail, node
from tracemind.agents.reporter import MARKER, SECTIONS, SOURCES_HEADING
from tracemind.config import settings


@node("validation")
async def validate_request(state):
    req = state["user_request"].strip()
    if not req:
        return fail(state, "validation", "empty_request", "Request is empty")
    if len(req) > settings.max_request_chars:
        return fail(state, "validation", "request_too_long", "Request exceeds size limit")
    return {}


@node("output_validation")
async def validate_output(state):
    md = state["report_md"] or ""
    for h in [*SECTIONS, SOURCES_HEADING]:
        if f"## {h}" not in md:
            return fail(state, "output_validation", "missing_section", h)
    if MARKER.search(md):
        return fail(state, "output_validation", "unresolved_marker", "Raw citation marker left in report")
    if not re.search(r"\[\d+\]", md):
        return fail(state, "output_validation", "no_citations", "Report has no citations")
    return {}


@node("completed")
async def finalize(state):
    # Phase 3: the approval interrupt goes right before this node.
    return {"status": "completed"}