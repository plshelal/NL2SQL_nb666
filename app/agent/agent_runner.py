"""agent_runner:把现有 14 节点 NL2SQL 图包装为 Agent Loop 的一个工具调用。

设计:
- 图一行不改;astream 以 stream_mode=["custom","updates"] 跑完
- 收集所有 SSE 事件透传给前端(思考过程/结果/图表/报告)
- 终态汇总成结构化 dict 返回给模型(need_clarify / missing_info / ok)
- 并发安全:Agent 循环可能并行调用本函数(行内+外部同时),共享 ctx 里的
  dw_session/meta_session 会触发 asyncmy "provisioning a new connection" 冲突。
  故每次调用创建独立 session(Qdrant/ES 客户端是线程安全可共享),用完即关。
"""
import time

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.core.log import logger


async def run_finance_pipeline(question: str, ctx: DataAgentContext,
                               writer=None, user_permissions: dict | None = None,
                               chat_context: dict | None = None, log_id: int | None = None,
                               formula_context: str = "", formula_indicators: list = None,
                               deep_thinking: bool = False) -> str:
    """执行行内问数流水线,返回给模型的 JSON 字符串结果。"""
    import json

    from app.clients.mysql_client_manager import (dw_mysql_client_manager,
                                                   meta_mysql_client_manager)
    from app.repositories.es.values_es_repository import ValueEsRepository
    from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
    from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
    from app.repositories.qdrant.column_qdrant_respository import ColumnQdrantRepository
    from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

    state: DataAgentState = {
        "query": question,
        "chat_context": chat_context or {},
        "user_permissions": user_permissions or {},
        "start_time": time.time(),
        "log_id": log_id,
        "retry_count": 0,
        "external_query": None,
        "external_queries": [],
        # 预处理层产物:组词已展开、公式已匹配,传给内部图跳过重复工作
        "formula_context": formula_context or "",
        "formula_indicators": formula_indicators or [],
        # 深度思考开关:generate_sql 据此选 llm(thinking) 或 llm_fast(无 thinking)
        "deep_thinking": deep_thinking,
    }

    final: dict = {}

    # 独立 session:每次调用一套自己的 MySQL 连接,与并行协程物理隔离
    # Qdrant/ES 客户端线程安全,可直接共享(ctx 里的 client)
    async with meta_mysql_client_manager.session_factory() as meta_session, \
             dw_mysql_client_manager.session_factory() as dw_session:
        meta_repo = MetaMysqlRepository(meta_session)
        dw_repo = DwMysqlRepository(dw_session)

        local_ctx: DataAgentContext = {
            "embeddings": ctx["embeddings"],
            "column_qdrant_repository": ColumnQdrantRepository(ctx["column_qdrant_repository"].client),
            "metric_qdrant_repository": MetricQdrantRepository(ctx["metric_qdrant_repository"].client),
            "value_es_repository": ValueEsRepository(ctx["value_es_repository"].client),
            "meta_mysql_repository": meta_repo,
            "dw_mysql_repository": dw_repo,
        }

        try:
            async for mode, chunk in graph.astream(
                input=state, context=local_ctx, stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    if isinstance(chunk, dict):
                        for key in ("result", "chart", "report", "hint", "note",
                                    "perm_rejected", "missing_info", "clarification",
                                    "perm_filtered", "sql"):
                            if key in chunk and key != "stage":
                                final[key] = chunk[key]
                        # 只透传 stage(思考流)和 chart(图表配置)给前端;
                        # result/report 不透传——表格由模型在 final_answer 里
                        # 用 markdown 呈现(前端 mdLite 渲染成 HTML 表格),
                        # 从结构上杜绝"中途表格先冒出来"的闪烁问题
                        if writer and ("stage" in chunk or "chart" in chunk):
                            writer(chunk)
                elif mode == "updates":
                    if isinstance(chunk, dict):
                        for node, upd in chunk.items():
                            if isinstance(upd, dict):
                                for key in ("route", "awaiting_clarification", "missing_info",
                                            "perm_rejected", "error"):
                                    if key in upd:
                                        final[key] = upd[key]
        except Exception as e:
            logger.error(f"[agent_runner] 流水线异常: {e}")
            return json.dumps({"status": "error", "error": str(e)[:300]}, ensure_ascii=False)

    # 结构化终态:模型据此决定"转述反问 / 补充信息 / 综合作答"
    if final.get("clarification"):
        out = {"status": "need_clarify",
               "question": final["clarification"].get("question", ""),
               "options": [o.get("label") for o in final["clarification"].get("options", [])]}
    elif final.get("perm_rejected"):
        out = {"status": "perm_rejected", "hint": final.get("hint", "无权访问")}
    elif final.get("missing_info"):
        out = {"status": "missing_info", "hint": final.get("hint", "缺少关键信息")}
    elif final.get("result") is not None and len(final.get("result", [])) > 0:
        out = {"status": "ok",
               "sql": final.get("sql", ""),
               "rows": final["result"][:50],
               "chart": final.get("chart"),
               "report": final.get("report", "")}
    else:
        out = {"status": "no_data", "hint": final.get("hint", "无数据")}

    logger.info(f"[agent_runner] 终态={out['status']} 耗时={time.time()-state['start_time']:.1f}s")
    return json.dumps(out, ensure_ascii=False, default=str)
