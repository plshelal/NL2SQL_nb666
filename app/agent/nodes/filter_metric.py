import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, MetricInfoState
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt


async def filter_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "过滤指标信息"})

    try:
        query = state["query"]
        metric_infos: list[MetricInfoState] = state["metric_infos"]

        tml = await loader_prompt("filter_metric_info")
        prompt = PromptTemplate(template=tml, input_variables=["query", "metric_infos"])
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser
        result = await chain.ainvoke({
            "query": query,
            "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False)
        })
        logger.info(f"指标信息过滤后的结果{result}")

        # 构造新列表,不原地修改 state 里的 metric_infos(保证幂等)
        new_metric_infos = [m for m in metric_infos if m["name"] in result]

        # 兜底：全部被过滤则保留原始召回结果
        if not new_metric_infos:
            logger.warning("所有指标被过滤，保留原始召回结果")
            new_metric_infos = metric_infos

        logger.info(f"过滤后的指标信息{[m['name'] for m in new_metric_infos]}")

        return {"metric_infos": new_metric_infos}
    except Exception as e:
        logger.error(f"过滤指标信息异常：{str(e)}")
        raise
