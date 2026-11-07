# tests/test_analyst.py
from tracemind.agents.analyst import make_analyst
from tracemind.llm import FakeLLM
from tracemind.schemas import AnalysisResult, Conclusion, PlatformSummary
from tests.helpers import state_with_findings


async def run(result):
    return await make_analyst(FakeLLM({"AnalysisResult": result}))(state_with_findings())


async def test_valid_analysis():
    out = await run(AnalysisResult(
        platforms=[PlatformSummary(name="Acme", pricing="$99/month", features=["scoring"],
                                   finding_ids=["f1", "f2"])],
        market_gaps=[Conclusion(statement="No pricing transparency elsewhere", finding_ids=["f1"])],
        missing_info=[],
    ))
    assert out["analysis"]["platforms"][0]["name"] == "Acme"
    assert out["current_stage"] == "analysis"


async def test_unknown_finding_id_fails():
    out = await run(AnalysisResult(
        platforms=[PlatformSummary(name="Acme", features=[], finding_ids=["f999"])],
        market_gaps=[], missing_info=[],
    ))
    assert out["status"] == "failed"
    assert out["errors"][0]["code"] == "invalid_citation"


async def test_pricing_without_citation_fails():
    out = await run(AnalysisResult(
        platforms=[PlatformSummary(name="Acme", pricing="$50/month", features=[], finding_ids=[])],
        market_gaps=[], missing_info=[],
    ))
    assert out["errors"][0]["code"] == "uncited_pricing"


async def test_unknown_ids_are_pruned_not_fatal():
    out = await run(AnalysisResult(
        platforms=[
            PlatformSummary(name="Acme", pricing="$99/month", features=[], finding_ids=["f1", "bogus"]),
            PlatformSummary(name="Ghost", features=[], finding_ids=["bogus2"]),
        ],
        market_gaps=[Conclusion(statement="unsupported idea", finding_ids=["bogus3"])],
        missing_info=[],
    ))
    assert out.get("status") != "failed"
    assert [p["name"] for p in out["analysis"]["platforms"]] == ["Acme"]
    assert out["analysis"]["platforms"][0]["finding_ids"] == ["f1"]
    assert out["analysis"]["market_gaps"] == []
    assert out["errors"][0]["code"] == "dropped_citation"


async def test_analyst_payload_has_no_verification_and_has_source_info():
    llm = FakeLLM({"AnalysisResult": AnalysisResult(
        platforms=[PlatformSummary(name="Acme", features=[], finding_ids=["f1"])],
        market_gaps=[], missing_info=[],
    )})
    await make_analyst(llm)(state_with_findings())
    user = llm.calls[0]["user"]
    assert "verification" not in user
    assert "source_url" in user