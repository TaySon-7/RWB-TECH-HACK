"""Small, public contracts shared by the graph, CLI and embedded client."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SQLRequest(Contract):
    sql: str = Field(description='One read-only query in the database dialect. Never Grafana macros.')


class SchemaRequest(Contract):
    tables: list[str] = Field(description='Qualified table names; [] returns the full available catalog.')


class QualityRequest(Contract):
    query_id: str = Field(description='ID returned by query_sql, not an invented ID.')


class ClarificationRequest(Contract):
    question: str = Field(min_length=1, max_length=1500, description='One short Russian question about material ambiguity.')
    options: list[str] = Field(min_length=2, max_length=3, description='Two or three mutually exclusive choices; a free-text answer is also accepted.')


class FinalAnswer(Contract):
    summary: str = Field(description='Brief Russian answer supported by actual query results.')
    query_ids: list[str] = Field(description='Executed queries supporting the answer.')
    limitations: list[str] = Field(description='Missing data, ambiguity or incomplete coverage; [] if none known.')


class QueryResult(Contract):
    query_id: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    tables: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    data_version: str | None = None
    quality: dict[str, Any] = Field(default_factory=dict)


class AgentResult(Contract):
    status: Literal['completed', 'partial', 'waiting', 'failed']
    summary: str = ''
    queries: list[QueryResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    clarification: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    artifact_dir: str | None = None
