import json

from langchain_core.embeddings import Embeddings

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.repositories.es.values_es_repository import ValueEsRepository
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
from app.repositories.qdrant.column_qdrant_respository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


class QueryService:


    def __init__(self,
                embeddings: Embeddings,
                column_qdrant_repository:ColumnQdrantRepository,
                metric_qdrant_repository:MetricQdrantRepository,
                value_es_repository:ValueEsRepository,
                meta_mysql_repository:MetaMysqlRepository,
                dw_mysql_repository: DwMysqlRepository
                ):
        self.embeddings = embeddings
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository



    # 智能体查询服务(Agent Loop 架构:模型看工具描述自主决策,快筛直通保纯内部零延迟)
    async def query(self, query: str, chat_context: dict = None, user_permissions: dict = None,
                    log_id: int = None, external_query: str = None):
        # 构建图上下文(工具内 14 节点流水线使用)
        context: DataAgentContext = {
            "embeddings": self.embeddings,
            "column_qdrant_repository": self.column_qdrant_repository,
            "metric_qdrant_repository": self.metric_qdrant_repository,
            "value_es_repository": self.value_es_repository,
            "meta_mysql_repository": self.meta_mysql_repository,
            "dw_mysql_repository": self.dw_mysql_repository,
        }

        # SSE writer:事件 → 前端
        queue: list[dict] = []
        import asyncio
        q: asyncio.Queue = asyncio.Queue()

        def writer(chunk: dict):
            q.put_nowait(chunk)

        from app.agent.orchestrator import run_agent_query
        task = asyncio.create_task(run_agent_query(
            query, context, writer, user_permissions, chat_context, log_id))

        # 流头先推 log_id:前端用它关联反馈(审核闭环起点)
        if log_id is not None:
            yield f"data:{json.dumps({'log_id': log_id}, ensure_ascii=False, default=str)}  \n\n"

        try:
            while not (task.done() and q.empty()):
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=0.2)
                    yield f"data:{json.dumps(chunk, ensure_ascii=False, default=str)}  \n\n"
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            yield f"data:{json.dumps({'error': str(e)}, ensure_ascii=False, default=str)}  \n\n"
