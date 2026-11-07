import pytest

from tracemind.mcp_client import ToolError, connect_stdio


async def test_real_server_lists_only_expected_tools():
    async with connect_stdio() as client:
        assert sorted(client.schemas) == ["fetch_page", "search_web"]


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/",
    "file:///etc/passwd",
])
async def test_real_server_blocks_ssrf(url):
    async with connect_stdio() as client:
        with pytest.raises(ToolError) as e:
            await client.call("researcher", "fetch_page", {"url": url})
        assert e.value.code == "tool_error"