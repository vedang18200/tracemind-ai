from tracemind.agents.verifier import make_verifier
from tracemind.llm import FakeLLM
from tracemind.schemas import VerificationOutput, VerificationResult
from tests.helpers import state_with_findings


def vr(fid, status="verified", reason="ok"):
    return VerificationResult(finding_id=fid, status=status, reason=reason)


async def run(results, state=None):
    llm = FakeLLM({"VerificationOutput": VerificationOutput(results=results)})
    return await make_verifier(llm)(state or state_with_findings())


async def test_all_verified():
    out = await run([vr("f1"), vr("f2")])
    assert all(r.status == "verified" for r in out["verification"])
    assert out["research_focus"] == []


async def test_missing_result_defaults_to_needs_review():
    out = await run([vr("f1")])
    f2 = next(r for r in out["verification"] if r.finding_id == "f2")
    assert f2.status == "needs_review"
    assert len(out["research_focus"]) == 1


async def test_number_not_in_excerpt_is_forced_unsupported():
    state = state_with_findings()
    state["findings"][0] = state["findings"][0].model_copy(update={"claim": "Acme Pro is $79/month"})
    out = await run([vr("f1"), vr("f2")], state)
    f1 = next(r for r in out["verification"] if r.finding_id == "f1")
    assert f1.status == "unsupported"      # model said verified, code overrode it


async def test_unknown_ids_from_model_are_ignored():
    out = await run([vr("f1"), vr("f2"), vr("ghost")])
    assert {r.finding_id for r in out["verification"]} == {"f1", "f2"}


async def test_missing_source_is_unsupported():
    state = state_with_findings()
    state["sources"] = []
    out = await run([vr("f1"), vr("f2")], state)
    assert all(r.status == "unsupported" for r in out["verification"]) 