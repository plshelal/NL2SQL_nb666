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

        # 1. 关键词(2026-08 精简:砍掉 LLM 扩展,jieba 分词直出——小库召回 top3,
        #    两步CoT Step1 会再做实体筛选,扩展环节净增益为负;省 1 次 LLM ~1.5s)
        column_kw = list(keywords)
        metric_kw = list(keywords)
        value_kw = list(keywords)
        # 多轮上下文补全:上轮问题里的实体词并入(保留"那B市呢"类指代解析能力)
        if ctx_text:
            import re as _re
            for w in _re.findall(r"[A-M]市|农商行|存贷|贷款|存款|不良|利润|客户|员工|网点", ctx_text):
                if w not in column_kw:
                    column_kw.append(w)
                if w not in metric_kw:
                    metric_kw.append(w)
                if w not in value_kw:
                    value_kw.append(w)

        # 2. Schema Link 锁定的指标（跳过向量搜索直接注入）
        linked = state.get("linked_indicators", [])
        if linked:
            logger.info(f"Schema Link 锁定指标: {linked}")
            # 直接注入到关键词列表中，省去这些指标的Qdrant搜索
            column_kw = list(set(column_kw + linked))
            metric_kw = list(set(metric_kw + linked))

        # 公式匹配已迁至 schema_link 第0段(2026-08 重构):
        # formula_context/formula_indicators 由上游写入 state,本节点不再查询——
        # 防止同一逻辑两处维护(expand 是关键词管道,公式命中属意图层)

        logger.info(f"扩展关键词: column={column_kw}, metric={metric_kw}, value={value_kw}")

        # 3. 批量向量化(col/met 关键词始终相同,统一一套 embedding 不重复计算)
        all_texts = list(set(keywords + column_kw + (linked or [])))
        all_embeddings = await embeddings.aembed_documents(all_texts)
        embed_map = dict(zip(all_texts, all_embeddings))
        shared_emb = [embed_map[t] for t in all_texts]

        return {
            "expanded_column_keywords": all_texts,
            "expanded_metric_keywords": all_texts,
            "expanded_value_keywords": value_kw,
            "col_embeddings": shared_emb,
            "met_embeddings": shared_emb,
        }
    except Exception as e:
        logger.error(f"扩展关键词异常：{str(e)}")
        raise
