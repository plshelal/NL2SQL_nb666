from typing import TypedDict

from app.models.es.value_info_es import ValueInfoEs
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant


class DBInfoState(TypedDict):
    version: str
    dialect: str

# 列信息封装实体
class ColumnInfoState(TypedDict):
    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list[str]

# 表信息封装实体
class TableInfoState(TypedDict):
    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]

# 指标信息封装实体
class MetricInfoState(TypedDict):
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]

class DataAgentState(TypedDict):
    query: str
    error: str
    keywords:list
    expanded_column_keywords: list
    expanded_metric_keywords: list
    expanded_value_keywords: list
    col_embeddings: list
    met_embeddings: list
    query_embedding: list  # 预算的原始问题 embedding(fewshot_rag 复用,免二次调 TEI)
    formula_context: str
    chat_context: dict
    linked_indicators: list
    link_type: str
    formula_indicators: list[str]
    perm_rejected: bool
    missing_info: bool
    user_permissions: dict
    retrieved_columns:list[ColumnInfoQdrant]
    retrieved_metrics:list[MetricInfoQdrant]
    retrieved_values:list[ValueInfoEs]
    table_infos:list[TableInfoState]
    metric_infos:list[MetricInfoState]
    db_info:DBInfoState
    sql:str
    start_time: float
    log_id: int
    retry_count: int
    route: str
    external_query: str
    external_queries: list
    schema_mode: str
    awaiting_clarification: bool
    clarification: dict
    assumed_interpretation: str
    external_result: list
