from tracemind.agents.researcher import make_researcher
from tracemind.llm import FakeLLM
from tracemind.schemas import ExtractedFinding, PageExtraction
from tracemind.state import new_state


def ext(claim, excerpt, conf=0.9):
    return PageExtraction(findings=[ExtractedFinding(claim=claim, excerpt=excerpt, confidence=conf)])


async def test_happy_path_builds_sources_and_findings():
    llm = FakeLLM({"PageExtraction": [
        ext("Acme Pro is $99/month", "Pro plan: $99/month"),
        ext("BetaHire has ATS sync", "ATS sync"),
        ext("Gamma is $149/month", "$149/month"),
    ]})
    out = await make_researcher(llm)(new_state("t", "u", "compare ai interview platforms"))
    assert len(out["sources"]) == 3
    assert len(out["findings"]) == 3
    assert all(f.source_id.startswith("src-") for f in out["findings"])
    assert out["research_rounds"] == 1


async def test_ungrounded_excerpt_is_dropped():
    llm = FakeLLM({"PageExtraction": [
        ext("Acme is free", "Acme is totally free forever"),   # not in page
        PageExtraction(findings=[]),
        PageExtraction(findings=[]),
    ]})
    state = new_state("t", "u", "x")
    state["findings"] = []  # empty
    out = await make_researcher(llm)(state)
    assert out["status"] == "failed"
    assert any(e["code"] == "ungrounded_finding" for e in out["errors"])
    assert out["errors"][-1]["code"] == "empty_research"


async def test_fetch_failure_does_not_stop_run():
    async def flaky_fetch(url):
        from tracemind.tools.stubs import fetch_page
        if url.endswith("beta"):
            raise TimeoutError("timeout")
        return await fetch_page(url)

    llm = FakeLLM({"PageExtraction": [
        ext("Acme Pro is $99/month", "Pro plan: $99/month"),
        ext("Gamma is $149/month", "$149/month"),
    ]})
    out = await make_researcher(llm, fetch=flaky_fetch)(new_state("t", "u", "x"))
    assert len(out["findings"]) == 2
    assert any(e["code"] == "fetch_failed" for e in out["errors"])


async def test_second_round_skips_known_sources_and_duplicate_claims():
    llm = FakeLLM({"PageExtraction": [
        ext("Acme Pro is $99/month", "Pro plan: $99/month"),
        ext("BetaHire has ATS sync", "ATS sync"),
        ext("Gamma is $149/month", "$149/month"),
    ]})
    researcher = make_researcher(llm)
    first = await researcher(new_state("t", "u", "x"))

    state = new_state("t", "u", "x")
    state["sources"], state["findings"] = first["sources"], first["findings"]
    state["research_rounds"] = 1
    out = await researcher(state)
    assert out["sources"] == []          # all URLs already known
    assert out["findings"] == []
    assert out["research_rounds"] == 2   # round still counts, no failure


async def test_page_content_is_wrapped_as_data():
    llm = FakeLLM({"PageExtraction": [PageExtraction(findings=[])] * 3})
    state = new_state("t", "u", "x")
    state["findings"] = []
    await make_researcher(llm)(state)
    assert all("<page url=" in c["user"] for c in llm.calls)
    assert "untrusted" in llm.calls[0]["system"].lower()