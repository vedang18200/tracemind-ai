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