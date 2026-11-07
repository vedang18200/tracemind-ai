import pytest

from tracemind.policy import ToolDenied, authorize


def test_researcher_may_search_and_fetch():
    authorize("researcher", "search_web")
    authorize("researcher", "fetch_page")


@pytest.mark.parametrize("agent,tool", [
    ("researcher", "send_report"),       # protected
    ("researcher", "query_database"),    # not allowlisted
    ("analyst", "search_web"),           # agent has no tools
    ("supervisor", "fetch_page"),
    ("nobody", "search_web"),
])
def test_denied(agent, tool):
    with pytest.raises(ToolDenied):
        authorize(agent, tool)