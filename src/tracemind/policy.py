AGENT_TOOLS: dict[str, set[str]] = {
    "researcher": {"search_web", "fetch_page"},
}

# Never callable through the generic tool client.
PROTECTED_TOOLS = {"send_report"}


class ToolDenied(PermissionError):
    pass


def authorize(agent: str, tool: str) -> None:
    if tool in PROTECTED_TOOLS:
        raise ToolDenied(f"{tool} requires approval and cannot be called directly")
    if tool not in AGENT_TOOLS.get(agent, set()):
        raise ToolDenied(f"agent {agent!r} is not permitted to call {tool!r}")