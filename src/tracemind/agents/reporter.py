import json
import re

from tracemind.agents.common import fail, load_prompt, node
from tracemind.llm import LLM
from tracemind.schemas import ReportDraft

RISKS = "Risks and limitations"
SECTIONS = [
    "Executive summary", "Research methodology", "Competitor overview",
    "Feature comparison", "Pricing comparison", "Market opportunities",
    RISKS, "Recommendations",
]
SOURCES_HEADING = "Sources and citations"
MARKER = re.compile(r"\[\[([^\]]+)\]\]")
REPORT_ATTEMPTS = 2


def _violations(draft: ReportDraft, findings: dict, status: dict) -> list[tuple[str, str]]:
    if [s.heading for s in draft.sections] != SECTIONS:
        return [("bad_structure", f"Sections must be exactly these, in order: {SECTIONS}")]
    out: list[tuple[str, str]] = []
    for sec in draft.sections:
        if not sec.body.strip():
            out.append(("empty_section", f"Section '{sec.heading}' is empty"))
        for fid in MARKER.findall(sec.body):
            if fid not in findings:
                out.append(("invalid_citation", f"Unknown finding id {fid} in '{sec.heading}'"))
            elif status.get(fid) != "verified" and sec.heading != RISKS:
                out.append(("unverified_claim_outside_risks",
                            f"{fid} is not verified; it may only be cited in '{RISKS}', not '{sec.heading}'"))
    return out


def make_reporter(llm: LLM):
    system = load_prompt("v3_reporter")

    @node("reporting")
    async def reporter(state):
        findings = {f.id: f for f in state["findings"]}
        sources = {s.id: s for s in state["sources"]}
        status = {r.finding_id: r.status for r in state["verification"]}

        stats = {
            "sources_collected": len(state["sources"]),
            "findings_collected": len(state["findings"]),
            "findings_verified": sum(r.status == "verified" for r in state["verification"]),
            "findings_needs_review": sum(r.status == "needs_review" for r in state["verification"]),
            "findings_unsupported": sum(r.status == "unsupported" for r in state["verification"]),
            "research_rounds": state["research_rounds"],
        }

        def view(f):
            s = sources.get(f.source_id)
            return {"id": f.id, "claim": f.claim, "verification": status.get(f.id, "unverified"),
                    "source_title": s.title if s else None}

        verified = [view(f) for f in findings.values() if status.get(f.id) == "verified"]
        unverified = [view(f) for f in findings.values() if status.get(f.id) != "verified"]

        base_user = (
            f"Task: {state['user_request']}\n\n"
            f"<run_stats>\n{json.dumps(stats)}\n</run_stats>\n\n"
            f"<verified_findings>\n{json.dumps(verified, indent=2)}\n</verified_findings>\n\n"
            f"<unverified_findings>\n{json.dumps(unverified, indent=2)}\n</unverified_findings>\n\n"
            f"<analysis>\n{json.dumps(state['analysis'], indent=2)}\n</analysis>"
        )

        user = base_user
        draft, violations = None, []
        for _ in range(REPORT_ATTEMPTS):
            draft = await llm.structured(system=system, user=user, schema=ReportDraft)
            violations = _violations(draft, findings, status)
            if not violations:
                break
            user = (base_user + "\n\nYour previous draft was rejected for these reasons:\n- "
                    + "\n- ".join(m for _, m in violations[:8])
                    + "\nRewrite the full report and fix every issue.")

        if violations:
            code, msg = violations[0]
            return fail(state, "reporting", code, msg)

        numbers: dict[str, int] = {}

        def number(m: re.Match) -> str:
            sid = findings[m.group(1)].source_id
            return f"[{numbers.setdefault(sid, len(numbers) + 1)}]"

        parts = ["# Competitor Analysis Report"]
        for sec in draft.sections:
            body = re.sub(r"(\[\d+\])(?:\s*\1)+", r"\1", MARKER.sub(number, sec.body))
            parts.append(f"## {sec.heading}\n\n{body}")

        lines = []
        for sid, n in numbers.items():
            s = sources.get(sid)
            if s:
                lines.append(f"{n}. [{s.title}]({s.url}) (retrieved {s.retrieved_at:%Y-%m-%d})")
        parts.append(f"## {SOURCES_HEADING}\n\n" + "\n".join(lines))

        return {"report_md": "\n\n".join(parts)}

    return reporter