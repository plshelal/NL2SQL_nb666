from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.es.value_info_es import ValueInfoEs


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "召回字段取值"})

    try:
        keywords = list(set(state["keywords"] + state.get("expanded_value_keywords", [])))
        value_es_repository = runtime.context["value_es_repository"]

        retrieved_value_map: dict[str, ValueInfoEs] = {}
        for keyword in keywords:
            values: list = await value_es_repository.search(keyword)
            if values:
                for value in values:
                    value_id = value["id"]
                    if value_id not in retrieved_value_map:
                        retrieved_value_map[value_id] = value

        retrieved_values = list(retrieved_value_map.values())
        logger.info(f"召回字段取值成功：{list(retrieved_value_map.keys())}")

        return {"retrieved_values": retrieved_values}
    except Exception as e:
        logger.error(f"召回字段取值异常：{str(e)}")
        raise
