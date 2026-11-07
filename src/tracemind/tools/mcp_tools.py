from pydantic import ValidationError

from tracemind.mcp_client import MCPToolClient, ToolError
from tracemind.tools.stubs import Page, SearchHit


def researcher_tools(client: MCPToolClient, agent: str = "researcher"):
    async def search(query: str) -> list[SearchHit]:
        data = await client.call(agent, "search_web", {"query": query})
        try:
            return [SearchHit(**h) for h in data["hits"]]
        except (KeyError, TypeError, ValidationError) as e:
            raise ToolError("invalid_output", f"search_web: {e}")

    async def fetch(url: str) -> Page:
        data = await client.call(agent, "fetch_page", {"url": url})
        try:
            return Page(url=data["url"], title=data["title"], text=data["text"])
        except (KeyError, TypeError, ValidationError) as e:
            raise ToolError("invalid_output", f"fetch_page: {e}")

    return search, fetch