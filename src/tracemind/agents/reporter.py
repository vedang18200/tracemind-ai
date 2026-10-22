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




def make_reporter(llm: LLM):
    system = load_prompt("v1_reporter")

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

        findings_payload = json.dumps([
            {"id": f.id, "claim": f.claim, "verification": status.get(f.id, "unverified"),
             "source_title": sources[f.source_id].title if f.source_id in sources else None}
            for f in findings.values()
        ], indent=2)


        draft = await llm.structured(
            system=system,
            user=(f"Task: {state['user_request']}\n\n<run_stats>\n{json.dumps(stats, indent=2)}\n</run_stats>\n\n"
                  f"<findings>\n{findings_payload}\n</findings>\n\n"
                  f"<analysis>\n{json.dumps(state['analysis'], indent=2)}\n</analysis>"),
            schema=ReportDraft,
        )

        if [s.heading for s in draft.sections] != SECTIONS:
            return fail(state, "reporting", "bad_structure", "Report sections missing or out of order")

        for sec in draft.sections:
            if not sec.body.strip():
                return fail(state, "reporting", "empty_section", f"Empty section: {sec.heading}")
            for fid in MARKER.findall(sec.body):
                if fid not in findings:
                    return fail(state, "reporting", "invalid_citation", f"Unknown finding id: {fid}")
                if status.get(fid) != "verified" and sec.heading != RISKS:
                    return fail(state, "reporting", "unverified_claim_outside_risks",
                                f"{fid} is not verified but cited in '{sec.heading}'")

        numbers: dict[str, int] = {}   # source_id -> citation number, by first use

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