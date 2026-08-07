from typing import Optional

from pydantic import BaseModel


class QuerySchema(BaseModel):
    query: str
    chat_context: Optional[dict] = None  # {prev_query, prev_sql, prev_result, entities}
