from langgraph.graph import END, START, StateGraph

from tracemind.agents.analyst import make_analyst
from tracemind.agents.lifecycle import finalize, validate_output, validate_request
from tracemind.agents.reporter import make_reporter
from tracemind.agents.researcher import make_researcher
from tracemind.agents.supervisor import make_supervisor
from tracemind.agents.verifier import make_verifier
from tracemind.llm import LLM
from tracemind.routing import route_after_verify
from tracemind.state import TraceMindState
from tracemind.tools.stubs import fetch_page, search_web


def _next_or_end(target: str):
    def route(state) -> str:
        return "end" if state["status"] == "failed" else target
    return route


def build_graph(llm: LLM, *, search=search_web, fetch=fetch_page, checkpointer=None):
    g = StateGraph(TraceMindState)

    g.add_node("validate", validate_request)
    g.add_node("supervisor", make_supervisor(llm))
    g.add_node("researcher", make_researcher(llm, search=search, fetch=fetch))
    g.add_node("analyst", make_analyst(llm))
    g.add_node("verifier", make_verifier(llm))
    g.add_node("reporter", make_reporter(llm))
    g.add_node("output_validate", validate_output)
    g.add_node("finalize", finalize)

    g.add_edge(START, "validate")
    for src, dst in [
        ("validate", "supervisor"), ("supervisor", "researcher"),
        ("researcher", "analyst"), ("analyst", "verifier"),
        ("reporter", "output_validate"), ("output_validate", "finalize"),
    ]:
        g.add_conditional_edges(src, _next_or_end(dst), {dst: dst, "end": END})

    g.add_conditional_edges(
        "verifier", route_after_verify,
        {"researcher": "researcher", "reporter": "reporter", "end": END},
    )
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)