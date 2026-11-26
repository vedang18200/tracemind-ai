import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from tracemind.mcp_client import ToolError, connect_http
from tracemind.mcp_servers.http_app import BearerTokenMiddleware, RateLimitMiddleware


# ---------- middleware unit tests (no network) ----------

async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _client(app, ip="1.2.3.4"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 1234)),
                             base_url="http://test")


async def test_bearer_rejects_missing_and_wrong_token():
    app = BearerTokenMiddleware(_ok_app, "s3cret")
    async with _client(app) as c:
        assert (await c.post("/mcp")).status_code == 401
        assert (await c.post("/mcp", headers={"Authorization": "Bearer nope"})).status_code == 401
        assert (await c.post("/mcp", headers={"Authorization": "Bearer s3cret"})).status_code == 200
        assert (await c.get("/healthz")).status_code == 200      # probe stays open


async def test_rate_limit_per_client_window():
    now = [0.0]
    app = RateLimitMiddleware(_ok_app, per_client=2, global_limit=100, clock=lambda: now[0])
    async with _client(app, "1.1.1.1") as a, _client(app, "2.2.2.2") as b:
        assert [(await a.post("/mcp")).status_code for _ in range(3)] == [200, 200, 429]
        assert (await b.post("/mcp")).status_code == 200          # other client unaffected
        r = await a.post("/mcp")
        assert r.status_code == 429 and r.headers["retry-after"] == "60"
        now[0] = 61.0                                             # window slides
        assert (await a.post("/mcp")).status_code == 200


async def test_rate_limit_global_cap():
    app = RateLimitMiddleware(_ok_app, per_client=100, global_limit=2, clock=lambda: 0.0)
    codes = []
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        async with _client(app, ip) as c:
            codes.append((await c.post("/mcp")).status_code)
    assert codes == [200, 200, 429]


async def test_forwarded_for_ignored_unless_trusted():
    app = RateLimitMiddleware(_ok_app, per_client=1, global_limit=100, clock=lambda: 0.0)
    async with _client(app) as c:
        assert (await c.post("/mcp", headers={"X-Forwarded-For": "9.9.9.1"})).status_code == 200
        # Spoofed header does not grant a fresh bucket.
        assert (await c.post("/mcp", headers={"X-Forwarded-For": "9.9.9.2"})).status_code == 429

    trusted = RateLimitMiddleware(_ok_app, per_client=1, global_limit=100, trust_proxy=True,
                                  clock=lambda: 0.0)
    async with _client(trusted) as c:
        # Proxy appends the real client last; spoofed leading entries are ignored.
        assert (await c.post("/mcp", headers={"X-Forwarded-For": "6.6.6.6, 5.5.5.5"})).status_code == 200
        assert (await c.post("/mcp", headers={"X-Forwarded-For": "7.7.7.7, 5.5.5.5"})).status_code == 429


# ---------- end-to-end: real uvicorn process, real MCP client ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server():
    port = _free_port()
    env = {**os.environ, "MCP_HTTP_HOST": "127.0.0.1", "MCP_HTTP_PORT": str(port),
           "MCP_AUTH_TOKEN": "test-token", "MCP_RATE_LIMIT_PER_MINUTE": "1000",
           "MCP_GLOBAL_RATE_LIMIT_PER_MINUTE": "1000"}
    proc = subprocess.Popen([sys.executable, "-m", "tracemind.mcp_servers.http_app"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/healthz", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            proc.kill()
            pytest.fail(f"server did not start: {proc.stderr.read().decode()[-2000:]}")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_http_server_lists_tools(http_server):
    async with connect_http(f"{http_server}/mcp", token="test-token") as client:
        assert sorted(client.schemas) == ["fetch_page", "search_web"]


async def test_http_server_blocks_ssrf(http_server):
    async with connect_http(f"{http_server}/mcp", token="test-token") as client:
        with pytest.raises(ToolError) as e:
            await client.call("researcher", "fetch_page", {"url": "http://169.254.169.254/latest/meta-data/"})
        assert e.value.code == "tool_error"


async def test_http_server_requires_token(http_server):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{http_server}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                         headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401


async def test_edge_client_ip_header_wins_over_forwarded_for():
    app = RateLimitMiddleware(_ok_app, per_client=1, global_limit=100, trust_proxy=True,
                              client_ip_header="CF-Connecting-IP", clock=lambda: 0.0)
    async with _client(app) as c:
        # Same proxy hop in X-Forwarded-For, different real visitors: separate buckets.
        h = {"X-Forwarded-For": "1.1.1.1, 10.0.0.9"}
        assert (await c.post("/mcp", headers={**h, "CF-Connecting-IP": "8.8.8.8"})).status_code == 200
        assert (await c.post("/mcp", headers={**h, "CF-Connecting-IP": "8.8.4.4"})).status_code == 200
        assert (await c.post("/mcp", headers={**h, "CF-Connecting-IP": "8.8.8.8"})).status_code == 429


def test_render_hostname_added_to_allow_list(monkeypatch):
    from tracemind.mcp_servers import http_app
    monkeypatch.setattr(http_app.settings, "mcp_allowed_hosts", [])
    monkeypatch.setattr(http_app.settings, "render_external_hostname", "tracemind-mcp.onrender.com")
    ts = http_app._transport_security()
    assert ts.enable_dns_rebinding_protection
    assert "tracemind-mcp.onrender.com" in ts.allowed_hosts
    assert "https://tracemind-mcp.onrender.com" in ts.allowed_origins


async def test_search_daily_budget(monkeypatch):
    from tracemind.mcp_servers import web_server
    monkeypatch.setattr(web_server.settings, "search_daily_limit", 2)
    monkeypatch.setattr(web_server, "_search_budget", {"day": "", "used": 0})
    web_server._take_search_budget()
    web_server._take_search_budget()
    with pytest.raises(RuntimeError, match="daily search limit"):
        web_server._take_search_budget()
