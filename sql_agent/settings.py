"""Explicit LLM/DB configuration; no credentials in prompts or artifacts."""
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=('.env.agent', 'sql_agent/.env'), extra='ignore', populate_by_name=True,
    )
    llm_model: str = Field(default='', validation_alias=AliasChoices('LLM_MODEL', 'OPENAI_MODEL'))
    llm_api_key: SecretStr = Field(default=SecretStr(''), validation_alias=AliasChoices('LLM_API_KEY', 'OPENAI_API_KEY'))
    llm_base_url: str | None = Field(default=None, validation_alias=AliasChoices('LLM_BASE_URL', 'OPENAI_BASE_URL'))
    llm_timeout: float = Field(default=90, gt=0, validation_alias='LLM_TIMEOUT_SECONDS')
    llm_max_tokens: int = Field(default=6000, gt=0, validation_alias='LLM_MAX_OUTPUT_TOKENS')
    llm_reasoning_effort: str = Field(default='none', validation_alias='LLM_REASONING_EFFORT')
    llm_responses: bool = Field(default=False, validation_alias='LLM_USE_RESPONSES_API')
    backend: Literal['postgres', 'clickhouse'] = Field(default='postgres', validation_alias='AGENT_DB_BACKEND')
    database_url: str = Field(default='postgresql://hack:hack@localhost:5433/marketplace', validation_alias='DATABASE_URL')
    clickhouse_url: str = Field(default='http://clickhouse:8123', validation_alias='CLICKHOUSE_URL')
    clickhouse_user: str = Field(default='agent_reader', validation_alias='CLICKHOUSE_AGENT_USER')
    clickhouse_password: SecretStr = Field(default=SecretStr(''), validation_alias='CLICKHOUSE_AGENT_PASSWORD')
    max_rows: int = Field(default=200, ge=1, le=5000, validation_alias='SQL_AGENT_MAX_ROWS')
    timeout_ms: int = Field(default=15000, ge=100, validation_alias='SQL_AGENT_TIMEOUT_MS')
    max_sql_repairs: int = Field(default=2, ge=0, le=5, validation_alias='SQL_AGENT_MAX_REPAIRS')
    max_tool_calls: int = Field(default=12, ge=3, le=30, validation_alias='SQL_AGENT_MAX_TOOL_CALLS')
    runtime_dir: Path = Field(default=Path('sql_agent/artifacts'), validation_alias='AGENT_RUNTIME_DIR')
    grafana_url: str = Field(default='http://grafana:3000', validation_alias='GRAFANA_URL')
    require_grafana_auth: bool = Field(default=False, validation_alias='AGENT_REQUIRE_GRAFANA_AUTH')
