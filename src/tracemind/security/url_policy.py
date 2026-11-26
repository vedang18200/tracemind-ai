import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpcore
import httpx

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}


class UrlNotAllowed(ValueError):
    pass


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global   # False for private, loopback, link-local, CGNAT, reserved


async def _getaddrinfo(host: str, port: int) -> list[str]:
    """Resolver hook (tests replace this to simulate DNS rebinding)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos]


async def resolve_public_ips(host: str, port: int) -> list[str]:
    """Resolve host and return its IPs, or raise if ANY of them is non-public."""
    host = host.strip("[]")
    try:
        ips = [str(ipaddress.ip_address(host))]          # literal IP
    except ValueError:
        try:
            ips = sorted(set(await _getaddrinfo(host, port)))
        except socket.gaierror:
            raise UrlNotAllowed(f"cannot resolve host: {host}")
    if not ips:
        raise UrlNotAllowed(f"cannot resolve host: {host}")
    for ip in ips:
        if not _is_public(ip):
            raise UrlNotAllowed(f"non-public address: {ip}")
    return ips


async def validate_url(url: str) -> str:
    """Static checks plus a DNS pre-check for clear errors. NOT sufficient on its own:
    the connection must go through pinned_transport() so the IP is re-checked at connect time."""
    try:
        parts = urlparse(url)
        port = parts.port
    except ValueError:
        raise UrlNotAllowed("malformed url")

    if parts.scheme not in ALLOWED_SCHEMES:
        raise UrlNotAllowed(f"scheme not allowed: {parts.scheme!r}")
    if not parts.hostname:
        raise UrlNotAllowed("missing host")
    if parts.username or parts.password:
        raise UrlNotAllowed("credentials in url not allowed")
    if port not in ALLOWED_PORTS:
        raise UrlNotAllowed(f"port not allowed: {port}")

    await resolve_public_ips(parts.hostname, port or 443)
    return url


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolves and validates at connect time, then dials the vetted IP literal.

    This closes the DNS-rebinding gap: validate_url() and the socket no longer resolve
    the name separately, so a domain can't answer "public" to the check and "private"
    to the connection. TLS is unaffected: httpcore still uses the original hostname
    for SNI and certificate verification.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None):
        self.inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ips = await resolve_public_ips(host, port)
        last_exc: Exception | None = None
        for ip in ips:                                    # try each vetted address, like the OS would
            try:
                return await self.inner.connect_tcp(ip, port, timeout=timeout,
                                                    local_address=local_address,
                                                    socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UrlNotAllowed("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


def pinned_transport(inner: httpcore.AsyncNetworkBackend | None = None) -> httpx.AsyncHTTPTransport:
    """httpx transport whose every connection goes through PinnedNetworkBackend.

    httpx has no public hook for the network backend, so we swap the pool it builds.
    tests/test_url_policy.py asserts this wiring so an httpx upgrade can't silently undo it.
    """
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        max_connections=10,
        max_keepalive_connections=5,
        keepalive_expiry=5.0,
        network_backend=PinnedNetworkBackend(inner),
    )
    return transport
