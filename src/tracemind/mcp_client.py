import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

import jsonschema
from pydantic import BaseModel

from tracemind.config import settings
from tracemind.policy import ToolDenied, authorize


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class ToolInvocation(BaseModel):
    tool_name: str
    agent: str
    arguments_summary: str
    status: Literal["ok", "error"]
    duration_ms: int
    error_code: str | None = None
    attempts: int = 0
    created_at: datetime


def _summarize(args: dict) -> str:
    return json.dumps(args, default=str)[:200]   # truncated, no full payloads


def _text(res) -> str:
    return " ".join(c.text for c in getattr(res, "content", []) if hasattr(c, "text"))[:300]


def _parse(res) -> dict:
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        if set(sc) == {"result"} and isinstance(sc["result"], dict):
            sc = sc["result"]
        return sc
    for c in getattr(res, "content", []):
        if getattr(c, "type", None) == "text":
            try:
                data = json.loads(c.text)
            except ValueError:
                break
            if isinstance(data, dict):
                return data
    raise ToolError("invalid_output", "tool returned no JSON object")


class MCPToolClient:
    def __init__(self, session):
        self.session = session
        self.schemas: dict[str, dict] = {}
        self.invocations: list[ToolInvocation] = []

    async def discover(self) -> list[str]:
        res = await self.session.list_tools()
        self.schemas = {t.name: t.inputSchema for t in res.tools}
        return sorted(self.schemas)

    async def call(self, agent: str, tool: str, args: dict) -> dict:
        start = time.monotonic()
        attempts = 0
        code: str | None = None
        try:
            try:
                authorize(agent, tool)
            except ToolDenied as e:
                raise ToolError("denied", str(e))

            schema = self.schemas.get(tool)
            if schema is None:
                raise ToolError("unknown_tool", f"server does not expose {tool}")
            try:
                jsonschema.validate(args, schema)
            except jsonschema.ValidationError as e:
                raise ToolError("invalid_arguments", e.message)

            while True:
                attempts += 1
                try:
                    return await self._call_once(tool, args)
                except ToolError as e:
                    if e.retryable and attempts <= settings.max_tool_retries:
                        await asyncio.sleep(settings.mcp_retry_backoff_seconds * attempts)
                        continue
                    raise
        except ToolError as e:
            code = e.code
            raise
        finally:
            self.invocations.append(ToolInvocation(
                tool_name=tool, agent=agent, arguments_summary=_summarize(args),
                status="error" if code else "ok",
                duration_ms=int((time.monotonic() - start) * 1000),
                error_code=code, attempts=attempts,
                created_at=datetime.now(timezone.utc),
            ))

    async def _call_once(self, tool: str, args: dict) -> dict:
        try:
            res = await asyncio.wait_for(
                self.session.call_tool(tool, args),
                timeout=settings.mcp_call_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise ToolError("timeout", f"{tool} exceeded {settings.mcp_call_timeout_seconds}s", retryable=True)
        except Exception as e:
            raise ToolError("unavailable", f"{type(e).__name__}: {e}", retryable=True)

        if res.isError:
            raise ToolError("tool_error", _text(res))      # not retried
        return _parse(res)


@asynccontextmanager
async def connect_stdio(command: str | None = None, args: list[str] | None = None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=command or sys.executable,
        args=args or ["-m", "tracemind.mcp_servers.web_server"],
        # Least privilege: the tool server gets only what it needs, not the LLM key.
        env={"PATH": os.environ.get("PATH", ""), "TAVILY_API_KEY": settings.tavily_api_key},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            try:
                await session.initialize()
                client = MCPToolClient(session)
                await client.discover()
            except Exception as e:
                raise ToolError("unavailable", f"MCP server failed to start: {e}")
            yield client