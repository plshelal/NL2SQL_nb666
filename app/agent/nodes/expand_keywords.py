from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger

EXPAND_PROMPT = """你是一个中文金融领域的关键词扩展专家。根据用户问题和对话上下文，扩展三组关键词用于后续检索。

对话上文：{context}
用户问题：{query}

注意：如果用户问题省略了关键信息（如只说了"B市呢"），必须从上文推断完整语义并补全关键词。例如上文在问存贷比，本文问"B市呢"，则关键词应包含存贷比和B市。

输出JSON格式：
{{
  "column_keywords": ["用于搜索数据库字段的关键词"],
  "metric_keywords": ["用于搜索业务指标的关键词"],
  "value_keywords": ["用于搜索字段取值的关键词，如地名、日期、机构名称等"]
}}
只输出JSON。"""


async def expand_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "扩展关键词"})

    try:
        query = state["query"]
        keywords = state["keywords"]
        embeddings = runtime.context["embeddings"]
        meta_repo = runtime.context["meta_mysql_repository"]

        # 构建对话上下文
        chat_ctx = state.get("chat_context", {})
        ctx_text = ""
        if chat_ctx and chat_ctx.get("prev_query"):
            ctx_text = f"上轮问题: {chat_ctx['prev_query']}"

        # 1. LLM 扩展关键词
        prompt = PromptTemplate(template=EXPAND_PROMPT, input_variables=["query", "context"])
        chain = prompt | llm | JsonOutputParser()
        result = await chain.ainvoke({"query": query, "context": ctx_text})

        column_kw = result.get("column_keywords", [])
        metric_kw = result.get("metric_keywords", [])
        value_kw = result.get("value_keywords", [])

        # 2. Schema Link 锁定的指标（跳过向量搜索直接注入）
        linked = state.get("linked_indicators", [])
        link_type = state.get("link_type", "normal")
        if linked:
            logger.info(f"Schema Link 锁定指标: {linked}")
            # 直接注入到关键词列表中，省去这些指标的Qdrant搜索
            column_kw = list(set(column_kw + linked))
            metric_kw = list(set(metric_kw + linked))

        # 3. 查指标公式表
        all_terms = list(set(keywords + column_kw + metric_kw + value_kw))
        formulas = await meta_repo.get_indicator_formulas(all_terms)
        formula_text = ""
        formula_indicators = []
        if formulas:
            items = []
            for term, f in formulas.items():
                items.append(f"- {term}: {f['description']}，SQL: {f['sql_template']}")
                formula_indicators.extend(f.get("index_names", []))
            formula_text = "【已知计算公式】\n" + "\n".join(items)
            logger.info(f"命中指标公式: {list(formulas.keys())}")

        logger.info(f"扩展关键词: column={column_kw}, metric={metric_kw}, value={value_kw}")

        # 3. 批量向量化
        col_keywords = list(set(keywords + column_kw))
        met_keywords = list(set(keywords + metric_kw))
        all_texts = list(set(col_keywords + met_keywords))

        all_embeddings = await embeddings.aembed_documents(all_texts)
        embed_map = dict(zip(all_texts, all_embeddings))

        return {
            "expanded_column_keywords": col_keywords,
            "expanded_metric_keywords": met_keywords,
            "expanded_value_keywords": value_kw,
            "col_embeddings": [embed_map[t] for t in col_keywords],
            "met_embeddings": [embed_map[t] for t in met_keywords],
            "formula_context": formula_text,
            "formula_indicators": formula_indicators,
        }
    except Exception as e:
        logger.error(f"扩展关键词异常：{str(e)}")
        raise
