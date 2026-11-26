from langgraph.graph import END, START, StateGraph

from tracemind.agents.analyst import make_analyst
from tracemind.agents.delivery import make_execute, make_gate, make_propose
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


def _after_output_validation(state) -> str:
    if state["status"] == "failed":
        return "end"
    return "propose_action" if state["deliver_to"] else "finalize"


def _after_gate(state) -> str:
    if state["status"] == "failed":
        return "end"
    return "finalize" if state["delivery"] == "rejected" else "execute_action"


def build_graph(llm: LLM, *, strong_llm: LLM | None = None, search=search_web, fetch=fetch_page,
                session_factory=None, checkpointer=None):
    strong = strong_llm or llm
    g = StateGraph(TraceMindState)

    g.add_node("validate", validate_request)
    g.add_node("supervisor", make_supervisor(llm))
    g.add_node("researcher", make_researcher(llm, search=search, fetch=fetch))
    g.add_node("analyst", make_analyst(strong))
    g.add_node("verifier", make_verifier(llm))
    g.add_node("reporter", make_reporter(strong))
    g.add_node("output_validate", validate_output)
    g.add_node("propose_action", make_propose(session_factory))
    g.add_node("approval_gate", make_gate(session_factory))
    g.add_node("execute_action", make_execute(session_factory))
    g.add_node("finalize", finalize)

    g.add_edge(START, "validate")
    for src, dst in [
        ("validate", "supervisor"), ("supervisor", "researcher"),
        ("researcher", "analyst"), ("analyst", "verifier"),
        ("reporter", "output_validate"),
        ("propose_action", "approval_gate"),
        ("execute_action", "finalize"),
    ]:
        g.add_conditional_edges(src, _next_or_end(dst), {dst: dst, "end": END})

    g.add_conditional_edges(
        "verifier", route_after_verify,
        {"researcher": "researcher", "reporter": "reporter", "end": END},
    )
    g.add_conditional_edges(
        "output_validate", _after_output_validation,
        {"propose_action": "propose_action", "finalize": "finalize", "end": END},
    )
    g.add_conditional_edges(
        "approval_gate", _after_gate,
        {"execute_action": "execute_action", "finalize": "finalize", "end": END},
    )
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)