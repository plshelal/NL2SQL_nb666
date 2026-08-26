from typing import Optional

from pydantic import BaseModel


class QuerySchema(BaseModel):
    query: str
    chat_context: Optional[dict] = None  # {prev_query, prev_sql, prev_result, entities}
    # 外部数据确认框回传:用户确认/编辑后的 iFinD 查询原文。
    # None=未走确认流程(首轮); ""=用户选择"仅查内部"; 非空=按此原文调用 MCP
    external_query: Optional[str] = None
    # 前端 toggle chips 开关
    external_enabled: bool = False  # 外部数据(MCP)开关
    deep_thinking: bool = False    # 深度思考开关(仅影响 generate_sql LLM)
