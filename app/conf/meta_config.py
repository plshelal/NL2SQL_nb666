from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class ColumnConfig:
    name: str
    role: str
    description: str
    alias: list[str]
    sync: bool


@dataclass
class TableConfig:
    name: str
    role: str
    description: str
    columns: list[ColumnConfig]


@dataclass
class MetricConfig:
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]


@dataclass
class MetaConfig:
    tables: Optional[list[TableConfig]] = None
    metrics: Optional[list[MetricConfig]] = None
    # indicator_groups 在 yaml 中存在但运行时不需要结构化校验,
    # 用 Any 兼容 OmegaConf 合并(yaml 里有什么都放进去,不报 ConfigKeyError)
    indicator_groups: Optional[list[Any]] = None
