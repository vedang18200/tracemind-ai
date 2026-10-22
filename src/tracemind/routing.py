from tracemind.config import settings


def unverified_ratio(state) -> float:
    results = state["verification"]
    if not results:
        return 1.0
    return sum(r.status != "verified" for r in results) / len(results)


def route_after_verify(state) -> str:
    if state["status"] == "failed":
        return "end"
    if (
        unverified_ratio(state) > settings.unsupported_ratio_threshold
        and state["research_rounds"] < settings.max_research_rounds
    ):
        return "researcher"
    return "reporter"