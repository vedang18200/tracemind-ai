import pytest

from tracemind.security.url_policy import UrlNotAllowed, validate_url

BAD = [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com",
    "http://127.0.0.1/",
    "http://127.0.0.1:8080/admin",
    "http://localhost/",
    "http://0.0.0.0/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",                 # IPv4-mapped IPv6
    "http://2130706433/",                         # decimal 127.0.0.1
    "http://user:pass@93.184.216.34/",            # credentials
    "http://93.184.216.34:22/",                   # odd port
    "http:///nohost",
    "",
]


@pytest.mark.parametrize("url", BAD)
async def test_blocked(url):
    with pytest.raises(UrlNotAllowed):
        await validate_url(url)


async def test_public_ip_literal_allowed():
    assert await validate_url("https://93.184.216.34/page") == "https://93.184.216.34/page"

# ---------- DNS rebinding: the IP is re-validated and pinned at connect time ----------

import httpcore
import httpx

from tracemind.mcp_servers import web_server
from tracemind.security import url_policy
from tracemind.security.url_policy import PinnedNetworkBackend, pinned_transport

PUBLIC, PRIVATE = "93.184.216.34", "127.0.0.1"


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stands in for the real socket layer: records where we would dial, never connects."""

    def __init__(self):
        self.dialed: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialed.append((host, port))
        raise httpcore.ConnectError("test backend does not connect")

    async def sleep(self, seconds):
        pass


def _resolver(monkeypatch, *answers):
    """Each lookup returns the next answer; the last one repeats."""
    calls = []

    async def fake(host, port):
        calls.append(host)
        return answers[min(len(calls), len(answers)) - 1]
    monkeypatch.setattr(url_policy, "_getaddrinfo", fake)
    return calls


@pytest.fixture
def recorder(monkeypatch):
    rec = RecordingBackend()
    monkeypatch.setattr(web_server, "pinned_transport", lambda: pinned_transport(inner=rec))
    return rec


async def test_rebinding_to_private_ip_is_blocked_before_dialing(monkeypatch, recorder):
    lookups = _resolver(monkeypatch, [PUBLIC], [PRIVATE])     # check sees public, connect sees private
    with pytest.raises(UrlNotAllowed, match="non-public"):
        await web_server.fetch_page("http://rebind.attacker.test/")
    assert len(lookups) == 2            # pre-check + connect-time check
    assert recorder.dialed == []        # never touched the socket layer


async def test_connection_dials_the_vetted_ip_not_the_hostname(monkeypatch, recorder):
    _resolver(monkeypatch, [PUBLIC])
    with pytest.raises(httpx.ConnectError):
        await web_server.fetch_page("http://example.test/")
    # A literal IP leaves the OS nothing to re-resolve, so no second DNS answer can sneak in.
    assert recorder.dialed == [(PUBLIC, 80)]


async def test_mixed_public_and_private_answer_is_blocked(monkeypatch):
    _resolver(monkeypatch, [PUBLIC, PRIVATE])
    rec = RecordingBackend()
    with pytest.raises(UrlNotAllowed):
        await PinnedNetworkBackend(rec).connect_tcp("multi.test", 443)
    assert rec.dialed == []


async def test_falls_through_to_next_vetted_ip(monkeypatch):
    _resolver(monkeypatch, [PUBLIC, "93.184.216.35"])
    rec = RecordingBackend()
    with pytest.raises(httpcore.ConnectError):
        await PinnedNetworkBackend(rec).connect_tcp("two.test", 443)
    assert rec.dialed == [(PUBLIC, 443), ("93.184.216.35", 443)]


async def test_unix_sockets_refused():
    with pytest.raises(UrlNotAllowed):
        await PinnedNetworkBackend(RecordingBackend()).connect_unix_socket("/var/run/docker.sock")


def test_pinned_transport_wiring_survives_httpx_upgrades():
    # pinned_transport() swaps a private httpx attribute; fail loudly if that stops working.
    t = pinned_transport()
    assert isinstance(t._pool, httpcore.AsyncConnectionPool)
    assert isinstance(t._pool._network_backend, PinnedNetworkBackend)
    client = httpx.AsyncClient(transport=t, trust_env=False)
    assert not client._mounts           # no env-proxy transport that would bypass the pin
