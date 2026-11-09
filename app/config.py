from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    elasticsearch_url: str = ""
    elasticsearch_api_key: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    default_group_id: str = "crew-demo"
    talks_index: str = "talks"
    messages_index: str = "messages"
    itineraries_index: str = "itineraries"
    query_logs_index: str = "query_logs"
    feedback_index: str = "feedback"
    llm_cache_index: str = "llm_cache"

    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
