"""Hosted entry point: serves the same tools as web_server.py over MCP Streamable HTTP.

Run locally:   python -m tracemind.mcp_servers.http_app
Clients:       POST http://<host>:<port>/mcp   (Claude Desktop, Cursor, VS Code, MCP Inspector)
Health check:  GET  /healthz
"""
import hmac
import json
import logging
import time
from collections import deque

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from tracemind.config import settings
from tracemind.mcp_servers.web_server import mcp

log = logging.getLogger("tracemind.mcp_http")

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
UNMETERED_PATHS = {"/healthz"}


async def _send_json(send, status: int, body: dict, headers: list[tuple[bytes, bytes]] = ()) -> None:
    payload = json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()), *headers],
    })
    await send({"type": "http.response.body", "body": payload})


class BearerTokenMiddleware:
    """Static shared-token auth. Constant-time compare; /healthz stays open for the platform's probe."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in UNMETERED_PATHS:
            return await self.app(scope, receive, send)
        got = dict(scope["headers"]).get(b"authorization", b"")
        if not hmac.compare_digest(got, self.expected):
            return await _send_json(send, 401, {"error": "unauthorized"},
                                    [(b"www-authenticate", b"Bearer")])
        return await self.app(scope, receive, send)


class RateLimitMiddleware:
    """Sliding 60s window, per client IP plus one global bucket.

    In-memory, so it is per process: fine for a single container demo. On Lambda or with
    several replicas, use API Gateway / load balancer throttling instead.
    """

    WINDOW = 60.0
    MAX_TRACKED_CLIENTS = 10_000

    def __init__(self, app, *, per_client: int, global_limit: int, trust_proxy: bool = False,
                 client_ip_header: str = "", clock=time.monotonic):
        self.app = app
        self.per_client = per_client
        self.global_limit = global_limit
        self.trust_proxy = trust_proxy
        self.client_ip_header = client_ip_header.lower().encode()
        self.clock = clock
        self.clients: dict[str, deque[float]] = {}
        self.all: deque[float] = deque()

    def _client_ip(self, scope) -> str:
        if self.client_ip_header:
            # Edge proxy overwrites this header (e.g. Cloudflare's CF-Connecting-IP on Render),
            # so callers can't spoof it. Only enable when the app is reachable solely via that proxy.
            ip = dict(scope["headers"]).get(self.client_ip_header, b"").decode("latin-1").strip()
            if ip:
                return ip
        if self.trust_proxy:
            xff = dict(scope["headers"]).get(b"x-forwarded-for", b"").decode("latin-1")
            if xff:
                # The proxy appends the address it saw; earlier entries are client-controlled.
                return xff.split(",")[-1].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.WINDOW:
            q.popleft()

    def _allow(self, ip: str) -> bool:
        now = self.clock()
        self._trim(self.all, now)
        if len(self.all) >= self.global_limit:
            return False
        q = self.clients.get(ip)
        if q is None:
            if len(self.clients) >= self.MAX_TRACKED_CLIENTS:   # bound memory under IP spraying
                for k in [k for k, v in self.clients.items() if not v or now - v[-1] >= self.WINDOW]:
                    del self.clients[k]
                if len(self.clients) >= self.MAX_TRACKED_CLIENTS:
                    return False
            q = self.clients[ip] = deque()
        self._trim(q, now)
        if len(q) >= self.per_client:
            return False
        q.append(now)
        self.all.append(now)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in UNMETERED_PATHS:
            return await self.app(scope, receive, send)
        if not self._allow(self._client_ip(scope)):
            return await _send_json(send, 429, {"error": "rate_limited"},
                                    [(b"retry-after", str(int(self.WINDOW)).encode())])
        return await self.app(scope, receive, send)


def _transport_security() -> TransportSecuritySettings:
    hosts = [h.strip() for h in settings.mcp_allowed_hosts if h.strip()]
    if settings.render_external_hostname and settings.render_external_hostname not in hosts:
        hosts.append(settings.render_external_hostname)
    if hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts + [f"{h}:*" for h in hosts] + LOCAL_HOSTS,
            allowed_origins=[f"https://{h}" for h in hosts] + ["http://127.0.0.1:*", "http://localhost:*"],
        )
    if settings.mcp_http_host in ("127.0.0.1", "localhost", "::1"):
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=LOCAL_HOSTS,
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    log.warning("MCP_ALLOWED_HOSTS is empty on a public bind; Host header checks are disabled")
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


async def healthz(_request):
    return JSONResponse({"status": "ok", "server": mcp.name})


def create_app():
    """Build the ASGI app. Call once per process: the MCP session manager can only run once."""
    # Stateless + JSON responses: every request is independent, so it scales horizontally
    # and survives container restarts / serverless cold starts.
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.transport_security = _transport_security()

    app = mcp.streamable_http_app()
    app.router.routes.insert(0, Route("/healthz", healthz, methods=["GET"]))

    wrapped = app
    if settings.mcp_auth_token:
        wrapped = BearerTokenMiddleware(wrapped, settings.mcp_auth_token)
    else:
        log.warning("MCP_AUTH_TOKEN is not set: anyone with the URL can call the tools")
    # Rate limit sits outermost so unauthenticated floods are also throttled.
    return RateLimitMiddleware(
        wrapped,
        per_client=settings.mcp_rate_limit_per_minute,
        global_limit=settings.mcp_global_rate_limit_per_minute,
        trust_proxy=settings.mcp_trust_proxy,
        client_ip_header=settings.mcp_client_ip_header,
    )


def main() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT") or settings.mcp_http_port)   # PaaS hosts (Render) inject PORT
    uvicorn.run(create_app(), host=settings.mcp_http_host, port=port,
                proxy_headers=False)   # client IP handling is done by RateLimitMiddleware


if __name__ == "__main__":
    main()
