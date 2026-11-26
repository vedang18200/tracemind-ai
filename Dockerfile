# Hosted TraceMind MCP server (search_web + fetch_page over Streamable HTTP).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MCP_HTTP_HOST=0.0.0.0 MCP_HTTP_PORT=8000

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"MCP_HTTP_PORT\"]}/healthz')"
CMD ["python", "-m", "tracemind.mcp_servers.http_app"]
