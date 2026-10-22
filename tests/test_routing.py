from tracemind.routing import route_after_verify
from tracemind.schemas import VerificationResult
from tests.helpers import state_with_findings


def state_with(statuses, rounds):
    s = state_with_findings()
    s["verification"] = [VerificationResult(finding_id=f"f{i}", status=st, reason="")
                         for i, st in enumerate(statuses)]
    s["research_rounds"] = rounds
    return s


def test_good_evidence_goes_to_reporter():
    assert route_after_verify(state_with(["verified", "verified"], 1)) == "reporter"


def test_weak_evidence_loops_back_once():
    assert route_after_verify(state_with(["verified", "unsupported"], 1)) == "researcher"


def test_loop_stops_at_round_limit():
    assert route_after_verify(state_with(["unsupported", "unsupported"], 2)) == "reporter"


def test_failed_state_ends():
    s = state_with(["verified"], 1)
    s["status"] = "failed"
    assert route_after_verify(s) == "end"
    