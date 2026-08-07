"""Schema Linking：一次LLM调用，从meta_config读取指标组，识别问题类型"""

from pathlib import Path

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime
from omegaconf import OmegaConf

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger


def _load_groups() -> dict:
    """从 meta_config.yaml 加载指标组定义"""
    config_path = Path(__file__).parent.parent.parent.parent / "conf" / "meta_config.yaml"
    config = OmegaConf.load(config_path)
    groups = {}
    if hasattr(config, "indicator_groups"):
        for g in config.indicator_groups:
            groups[g.name] = {"description": g.description, "indicators": list(g.indicators)}
    return groups


LINK_PROMPT = """你是一个金融数据库专家。根据用户问题，判断属于哪种查询类型。

用户问题：{query}

指标组定义（名称、描述、包含的指标）：
{groups}

请输出JSON：
{{
  "type": "group | computed | normal",
  "groups": ["匹配到的指标组名列表"],
  "reason": "简短判断理由"
}}

- type="group": 问题提到了指标组概念（如规模、质量、效益、结构等），列出匹配的组名
- type="computed": 问题是某个计算指标（如存贷比、人均存款），但不在组定义里
- type="normal": 问题直接提到了具体指标名（如各项存款余额），不需要组匹配

只输出JSON。"""


async def schema_link(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "语义分析"})

    try:
        query = state["query"]
        INDICATOR_GROUPS = _load_groups()

        groups_text = "\n".join([f"- {k}({v['description']}): {', '.join(v['indicators'])}" for k, v in INDICATOR_GROUPS.items()])

        prompt = PromptTemplate(template=LINK_PROMPT, input_variables=["query", "groups"])
        chain = prompt | llm | JsonOutputParser()
        result = await chain.ainvoke({"query": query, "groups": groups_text})

        linked_indicators = []
        link_type = result.get("type", "normal")
        matched_groups = result.get("groups", [])

        if link_type in ("group", "computed"):
            for g in matched_groups:
                if g in INDICATOR_GROUPS:
                    linked_indicators.extend(INDICATOR_GROUPS[g]["indicators"])
            linked_indicators = list(set(linked_indicators))

        logger.info(f"Schema Link: type={link_type}, groups={matched_groups}, indicators={linked_indicators[:5]}...")

        return {
            "linked_indicators": linked_indicators,
            "link_type": link_type,
        }
    except Exception as e:
        logger.error(f"Schema Link异常：{str(e)}")
        raise
