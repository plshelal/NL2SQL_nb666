import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TableInfoState
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "过滤表信息"})

    try:
        query = state["query"]
        table_infos: list[TableInfoState] = state["table_infos"]
        # 保存原始副本——下面循环会原地修改 table_infos，state["table_infos"] 也会被改
        orig_table_infos = [{ "name": t["name"], "role": t.get("role",""), "description": t.get("description",""), "columns": [dict(c) for c in t.get("columns",[])] } for t in table_infos]

        tml = await loader_prompt("filter_table_info")
        prompt = PromptTemplate(template=tml, input_variables=["query", "table_infos"])
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser
        result = await chain.ainvoke({
            "query": query,
            "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False)
        })
        logger.info(f"表信息过滤后的结果{result}")

        # 构造新列表,不原地修改 state 里的 table_infos(保证幂等)
        # 原始列副本,用于兜底补回关键列
        orig_cols_by_table = {t["name"]: {c["name"]: c for c in t.get("columns", [])} for t in orig_table_infos}

        new_table_infos = []
        for table_info in table_infos:
            table_name = table_info["name"]
            if table_name not in result:
                continue
            kept_cols = [c for c in table_info.get("columns", []) if c["name"] in result[table_name]]
            new_table_infos.append({**table_info, "columns": kept_cols})

        # 兜底：fact 表(index_data)至少要保留 index_name/index_value/data_date/org_code
        # index_list 至少要保留 index_name/index_unit/index_desc
        for table_info in new_table_infos:
            if table_info["name"] in ("index_data", "index_list"):
                required = ["index_name", "index_value", "data_date", "org_code"] \
                    if table_info["name"] == "index_data" else ["index_name", "index_unit", "index_desc"]
                current_cols = [c["name"] for c in table_info["columns"]]
                orig_cols = orig_cols_by_table.get(table_info["name"], {})
                for req in required:
                    if req not in current_cols and req in orig_cols:
                        table_info["columns"].append(orig_cols[req])
                        logger.warning(f"过滤太激进，补回关键列: {table_info['name']}.{req}")

        # 兜底：全部被过滤则保留原始
        if not new_table_infos:
            logger.warning("所有表被过滤，保留原始召回结果")
            new_table_infos = orig_table_infos

        # 兜底：org_info 不能被删
        if not any(t["name"] == "org_info" for t in new_table_infos):
            for ot in orig_table_infos:
                if ot["name"] == "org_info":
                    new_table_infos.append(ot)
                    logger.warning("过滤遗漏 org_info，补回")
                    break

        logger.info(f"过滤后的表信息{[table_info['name'] for table_info in new_table_infos]}")

        return {"table_infos": new_table_infos}
    except Exception as e:
        logger.error(f"过滤表信息异常：{str(e)}")
        raise
