# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    max_agent_steps: int = 25
    max_research_rounds: int = 2
    max_tool_retries: int = 2
    task_timeout_seconds: int = 600
    unsupported_ratio_threshold: float = 0.3   # above this, trigger targeted research
    max_sources: int = 20
    max_request_chars: int = 4000
    llm_model: str = ""
    llm_max_tokens: int = 8192
    llm_validation_retries: int = 1
    tavily_api_key: str = ""
    fetch_max_bytes: int = 1_000_000
    fetch_max_chars: int = 20_000
    fetch_timeout_seconds: float = 10.0
    fetch_max_redirects: int = 3
    mcp_call_timeout_seconds: float = 30.0
    mcp_retry_backoff_seconds: float = 0.5
    max_findings_per_page: int = 6
    max_total_findings: int = 45
    database_url: str = "postgresql+psycopg://tracemind:tracemind@localhost:5432/tracemind"
    allow_self_approval: bool = False
    allowed_recipient_domains: list[str] = [] 
    llm_model_strong: str = ""
    # Hosted MCP server (Streamable HTTP)
    mcp_url: str = ""                          # client side: use a remote server instead of stdio
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8000
    mcp_auth_token: str = ""                   # empty = open server (rate limit still applies)
    mcp_allowed_hosts: list[str] = []          # public hostnames, e.g. ["tracemind-mcp.onrender.com"]
    mcp_rate_limit_per_minute: int = 30        # per client IP
    mcp_global_rate_limit_per_minute: int = 120  # whole instance; protects the search quota
    mcp_trust_proxy: bool = False              # read client IP from X-Forwarded-For (behind a load balancer)
    mcp_client_ip_header: str = ""            # header set by the edge proxy, e.g. cf-connecting-ip on Render
    render_external_hostname: str = ""        # injected by Render; auto-added to the Host allow-list
    search_daily_limit: int = 0                # max search_web calls per UTC day per process; 0 = unlimited

settings = Settings()