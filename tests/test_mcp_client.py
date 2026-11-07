import asyncio
from types import SimpleNamespace as NS

import pytest

from tracemind.config import settings
from tracemind.mcp_client import MCPToolClient, ToolError
from tracemind.tools.mcp_tools import researcher_tools

SEARCH = NS(name="search_web", inputSchema={
    "type": "object",
    "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
    "required": ["query"],
})
FETCH = NS(name="fetch_page", inputSchema={
    "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"],
})


def ok(data):
    return NS(isError=False, structuredContent=data, content=[])


class FakeSession:
    def __init__(self, handler, tools=(SEARCH, FETCH)):
        self.handler, self.tools, self.calls = handler, list(tools), []

    async def list_tools(self):
        return NS(tools=self.tools)

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return await self.handler(name, args)


@pytest.fixture(autouse=True)
def fast_settings(monkeypatch):
    monkeypatch.setattr(settings, "mcp_retry_backoff_seconds", 0)
    monkeypatch.setattr(settings, "max_tool_retries", 1)
    monkeypatch.setattr(settings, "mcp_call_timeout_seconds", 0.05)


async def make(handler, **kw):
    session = FakeSession(handler, **kw)
    client = MCPToolClient(session)
    await client.discover()
    return client, session


async def test_discovery_and_success_logged():
    async def h(name, args):
        return ok({"hits": [{"url": "https://a.test", "title": "A", "snippet": "s"}]})
    client, _ = await make(h)
    assert await client.discover() == ["fetch_page", "search_web"]
    search, _ = researcher_tools(client)
    hits = await search("q")
    assert hits[0].url == "https://a.test"
    inv = client.invocations[-1]
    assert inv.status == "ok" and inv.tool_name == "search_web" and inv.attempts == 1


async def test_denied_tool_never_reaches_server():
    async def h(name, args): return ok({})
    client, session = await make(h)
    with pytest.raises(ToolError) as e:
        await client.call("researcher", "send_report", {})
    assert e.value.code == "denied"
    assert session.calls == []
    assert client.invocations[-1].error_code == "denied"


async def test_tool_not_exposed_by_server():
    async def h(name, args): return ok({})
    client, session = await make(h, tools=[SEARCH])
    with pytest.raises(ToolError) as e:
        await client.call("researcher", "fetch_page", {"url": "https://a.test"})
    assert e.value.code == "unknown_tool" and session.calls == []


async def test_invalid_arguments_rejected_before_call():
    async def h(name, args): return ok({})
    client, session = await make(h)
    with pytest.raises(ToolError) as e:
        await client.call("researcher", "fetch_page", {"url": 123})
    assert e.value.code == "invalid_arguments" and session.calls == []


async def test_timeout_retried_once_then_fails():
    async def h(name, args):
        await asyncio.sleep(1)
    client, session = await make(h)
    with pytest.raises(ToolError) as e:
        await client.call("researcher", "fetch_page", {"url": "https://a.test"})
    assert e.value.code == "timeout"
    assert len(session.calls) == 2                 # 1 try + 1 retry
    assert client.invocations[-1].attempts == 2


async def test_transient_failure_recovers_on_retry():
    state = {"n": 0}
    async def h(name, args):
        state["n"] += 1
        if state["n"] == 1:
            raise ConnectionError("server hiccup")
        return ok({"url": "https://a.test", "title": "A", "text": "hello"})
    client, _ = await make(h)
    _, fetch = researcher_tools(client)
    page = await fetch("https://a.test")
    assert page.text == "hello"
    assert client.invocations[-1].attempts == 2 and client.invocations[-1].status == "ok"


async def test_tool_error_not_retried():
    async def h(name, args):
        return NS(isError=True, structuredContent=None, content=[NS(type="text", text="non-public address")])
    client, session = await make(h)
    with pytest.raises(ToolError) as e:
        await client.call("researcher", "fetch_page", {"url": "http://127.0.0.1"})
    assert e.value.code == "tool_error"
    assert len(session.calls) == 1


async def test_malformed_output_rejected():
    async def h(name, args): return ok({"unexpected": "shape"})
    client, _ = await make(h)
    search, fetch = researcher_tools(client)
    with pytest.raises(ToolError) as e:
        await search("q")
    assert e.value.code == "invalid_output"
    with pytest.raises(ToolError):
        await fetch("https://a.test")


async def test_non_json_text_output_rejected():
    async def h(name, args):
        return NS(isError=False, structuredContent=None, content=[NS(type="text", text="not json")])
    client, _ = await make(h)
    with pytest.raises(ToolError) as e:
        await client.call("researcher", "search_web", {"query": "q"})
    assert e.value.code == "invalid_output"