from tracemind.agents.reporter import RISKS, SECTIONS
from tracemind.agents.researcher import _source_id
from tracemind.graph import build_graph
from tracemind.llm import FakeLLM
from tracemind.schemas import (
    AnalysisResult, Conclusion, ExtractedFinding, PageExtraction, PlanStep,
    PlatformSummary, ReportDraft, ReportSection, SupervisorPlan,
    VerificationOutput, VerificationResult,
)
from tracemind.state import new_state

URLS = {
    "acme": "https://example.test/acme",
    "beta": "https://example.test/beta",
    "gamma": "https://example.test/gamma",
}


def fid(key: str) -> str:
    return f"f-{_source_id(URLS[key])}-0"


def extraction(claim, excerpt):
    return PageExtraction(findings=[ExtractedFinding(claim=claim, excerpt=excerpt, confidence=0.9)])


def plan():
    agents = ["researcher", "analyst", "verifier", "reporter"]
    return SupervisorPlan(
        task_type="competitor_analysis",
        steps=[PlanStep(id=f"s{i}", description=a, agent=a) for i, a in enumerate(agents, 1)],
        search_queries=["ai interview platforms pricing"],
    )


def analysis():
    return AnalysisResult(
        platforms=[PlatformSummary(name="Acme", pricing="$99/month", features=["scoring"],
                                   finding_ids=[fid("acme")])],
        market_gaps=[Conclusion(statement="Pricing is opaque", finding_ids=[fid("beta")])],
        missing_info=["BetaHire pricing"],
    )


def verification(unsupported=()):
    return VerificationOutput(results=[
        VerificationResult(finding_id=fid(k), status="unsupported" if k in unsupported else "verified",
                           reason="test")
        for k in URLS
    ])


def draft(pricing_ids, risk_ids=()):
    def body(heading):
        if heading == "Pricing comparison":
            return "Prices: " + " ".join(f"[[{i}]]" for i in pricing_ids)
        if heading == RISKS:
            return "Limits. " + " ".join(f"[[{i}]]" for i in risk_ids)
        if heading == "Competitor overview":
            return f"BetaHire has ATS sync [[{fid('beta')}]]"
        return "Text."
    return ReportDraft(sections=[ReportSection(heading=h, body=body(h)) for h in SECTIONS])


def base_responses(**overrides):
    r = {
        "SupervisorPlan": plan(),
        "PageExtraction": [
            extraction("Acme Pro is $99/month", "Pro plan: $99/month"),
            extraction("BetaHire has ATS sync", "ATS sync"),
            extraction("Gamma is $149/month", "$149/month"),
        ],
        "AnalysisResult": [analysis(), analysis()],
        "VerificationOutput": [verification()],
        "ReportDraft": draft([fid("acme"), fid("gamma")]),
    }
    r.update(overrides)
    return r


def count(llm, schema):
    return sum(c["schema"] == schema for c in llm.calls)


async def run(llm, request="compare ai interview platforms"):
    return await build_graph(llm).ainvoke(new_state("t1", "u1", request))


async def test_happy_path_completes():
    llm = FakeLLM(base_responses())
    out = await run(llm)
    assert out["status"] == "completed"
    assert out["current_stage"] == "completed"
    assert "## Sources and citations" in out["report_md"]
    assert "[[" not in out["report_md"]
    assert "[1]" in out["report_md"]
    assert count(llm, "VerificationOutput") == 1
    assert out["research_rounds"] == 1


async def test_weak_evidence_triggers_one_extra_round_then_reports():
    llm = FakeLLM(base_responses(
        VerificationOutput=[verification(unsupported={"gamma"}), verification()],
    ))
    out = await run(llm)
    assert out["status"] == "completed"
    assert out["research_rounds"] == 2
    assert count(llm, "VerificationOutput") == 2


async def test_unverified_claim_outside_risks_fails():
    llm = FakeLLM(base_responses(
        VerificationOutput=[verification(unsupported={"gamma"})] * 2,
        ReportDraft=draft([fid("acme"), fid("gamma")]),     # gamma cited in Pricing
    ))
    out = await run(llm)
    assert out["status"] == "failed"
    assert out["errors"][-1]["code"] == "unverified_claim_outside_risks"
    assert out["report_md"] is None


async def test_unverified_claim_in_risks_is_allowed():
    llm = FakeLLM(base_responses(
        VerificationOutput=[verification(unsupported={"gamma"})] * 2,
        ReportDraft=draft([fid("acme")], risk_ids=[fid("gamma")]),
    ))
    out = await run(llm)
    assert out["status"] == "completed"


async def test_unsupported_task_ends_early():
    llm = FakeLLM(base_responses(SupervisorPlan=SupervisorPlan(task_type="unsupported", steps=[])))
    out = await run(llm)
    assert out["status"] == "failed"
    assert count(llm, "PageExtraction") == 0


async def test_empty_request_rejected_before_any_llm_call():
    llm = FakeLLM(base_responses())
    out = await run(llm, request="   ")
    assert out["errors"][0]["code"] == "empty_request"
    assert llm.calls == []


async def test_node_crash_becomes_failed_state():
    responses = base_responses()
    del responses["SupervisorPlan"]                         # FakeLLM raises KeyError
    out = await run(FakeLLM(responses))
    assert out["status"] == "failed"
    assert out["errors"][0]["code"] == "node_exception"
    
async def test_reporter_gets_run_stats():
    llm = FakeLLM(base_responses())
    await run(llm)
    reporter_call = next(c for c in llm.calls if c["schema"] == "ReportDraft")
    assert "<run_stats>" in reporter_call["user"]
    assert '"findings_collected": 3' in reporter_call["user"]

async def test_reporter_retries_with_feedback_then_succeeds():
    llm = FakeLLM(base_responses(
        VerificationOutput=[verification(unsupported={"gamma"})] * 2,
        ReportDraft=[
            draft([fid("acme"), fid("gamma")]),                    # breaks the rule
            draft([fid("acme")], risk_ids=[fid("gamma")]),         # fixed
        ],
    ))
    out = await run(llm)
    assert out["status"] == "completed"
    assert count(llm, "ReportDraft") == 2
    retry_call = [c for c in llm.calls if c["schema"] == "ReportDraft"][1]
    assert "not verified" in retry_call["user"]