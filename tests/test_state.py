# tests/test_state.py
import pytest
from pydantic import BaseModel, ValidationError

from tracemind.llm import FakeLLM
from tracemind.schemas import Finding
from tracemind.state import SCHEMA_VERSION, new_state


def make_finding(**overrides) -> dict:
    data = dict(
        id="f1",
        claim="Platform X costs $99/mo",
        source_id="s1",
        excerpt="Pricing starts at $99 per month",
        confidence=0.8,
    )
    data.update(overrides)
    return data


def test_new_state_has_all_keys():
    s = new_state("t1", "u1", "compare platforms")
    expected = {
        "schema_version", "task_id", "user_id", "user_request", "plan", "findings",
        "sources", "analysis", "verification", "report_md", "approval_id", "status",
        "current_stage", "step_count", "research_rounds", "errors","research_focus",
    }
    assert set(s.keys()) == expected
    assert s["schema_version"] == SCHEMA_VERSION
    assert s["status"] == "running"


def test_finding_valid():
    f = Finding(**make_finding())
    assert f.verification == "unverified"


def test_finding_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        Finding(**make_finding(confidence=1.5))


def test_finding_rejects_non_numeric_confidence():
    with pytest.raises(ValidationError):
        Finding(**make_finding(confidence="high"))


def test_finding_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Finding(**make_finding(extra_field=1))


class Dummy(BaseModel):
    n: int


@pytest.mark.asyncio
async def test_fakellm_returns_in_order_and_records_calls():
    llm = FakeLLM({"Dummy": [Dummy(n=1), Dummy(n=2)]})
    assert (await llm.structured(system="s", user="u", schema=Dummy)).n == 1
    assert (await llm.structured(system="s", user="u", schema=Dummy)).n == 2
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_fakellm_raises_when_exhausted():
    llm = FakeLLM({"Dummy": [Dummy(n=1)]})
    await llm.structured(system="s", user="u", schema=Dummy)
    with pytest.raises(RuntimeError):
        await llm.structured(system="s", user="u", schema=Dummy)


@pytest.mark.asyncio
async def test_fakellm_does_not_mutate_input_list():
    fixtures = [Dummy(n=1), Dummy(n=2)]
    llm = FakeLLM({"Dummy": fixtures})
    await llm.structured(system="s", user="u", schema=Dummy)
    assert len(fixtures) == 2