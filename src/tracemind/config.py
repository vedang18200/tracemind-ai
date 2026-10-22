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
    llm_max_tokens: int = 4096
    llm_validation_retries: int = 1

settings = Settings()