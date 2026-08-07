import asyncio

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "召回字段信息"})

    try:
        column_qdrant_repository = runtime.context["column_qdrant_repository"]
        embeddings = state.get("col_embeddings", [])

        # 并行搜索 Qdrant
        async def search_one(embedding):
            return await column_qdrant_repository.search(embedding)

        results = await asyncio.gather(*[search_one(e) for e in embeddings])

        retrieved_column_map: dict[str, ColumnInfoQdrant] = {}
        for payloads in results:
            for payload in payloads:
                column_id = payload["id"]
                if column_id not in retrieved_column_map:
                    retrieved_column_map[column_id] = payload

        retrieved_columns = list(retrieved_column_map.values())
        logger.info(f"召回字段信息成功，{list(retrieved_column_map.keys())}")

        return {"retrieved_columns": retrieved_columns}
    except Exception as e:
        logger.error(f"召回字段信息异常，{str(e)}")
        raise
