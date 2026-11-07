import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}


class UrlNotAllowed(ValueError):
    pass


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global   # False for private, loopback, link-local, CGNAT, reserved


async def validate_url(url: str) -> str:
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

    host = parts.hostname
    try:
        ips = [str(ipaddress.ip_address(host))]          # literal IP
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror:
            raise UrlNotAllowed(f"cannot resolve host: {host}")
        ips = sorted({i[4][0] for i in infos})

    for ip in ips:
        if not _is_public(ip):
            raise UrlNotAllowed(f"non-public address: {ip}")
    return url
