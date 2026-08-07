import asyncio

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "召回指标信息"})

    try:
        metric_qdrant_repository = runtime.context["metric_qdrant_repository"]
        embeddings = state.get("met_embeddings", [])

        async def search_one(embedding):
            return await metric_qdrant_repository.search(embedding)

        results = await asyncio.gather(*[search_one(e) for e in embeddings])

        retrieved_metric_map: dict[str, MetricInfoQdrant] = {}
        for payloads in results:
            for payload in payloads:
                metric_id = payload["id"]
                if metric_id not in retrieved_metric_map:
                    retrieved_metric_map[metric_id] = payload

        retrieved_metrics = list(retrieved_metric_map.values())
        logger.info(f"召回指标信息成功，{list(retrieved_metric_map.keys())}")

        return {"retrieved_metrics": retrieved_metrics}
    except Exception as e:
        logger.error(f"召回指标信息异常，{str(e)}")
        raise
