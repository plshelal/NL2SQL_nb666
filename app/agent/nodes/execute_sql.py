import json
import re
import time
from datetime import datetime
from sqlalchemy import text

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt

# 从生成的 SQL 中精确提取 index_name='xxx' / org_name='xxx' 的值
_RE_INDS = re.compile(r"index_name\s*=\s*'([^']+)'", re.IGNORECASE)
_RE_ORGS = re.compile(r"org_name\s*=\s*'([^']+)'", re.IGNORECASE)

ALL_ORGS = {
    "江苏省A市农商行","江苏省B市农商行","江苏省C市农商行","江苏省D市农商行",
    "江苏省E市农商行","江苏省F市农商行","江苏省G市农商行","江苏省H市农商行",
    "江苏省I市农商行","江苏省J市农商行","江苏省K市农商行","江苏省L市农商行","江苏省M市农商行"
}
ALL_INDS = {
    "各项存款余额","各项贷款余额","对公存款余额","个人存款余额","对公贷款余额",
    "个人贷款余额","中间业务收入","净利息收入","营业收入","营业支出","净利润",
    "成本收入比","不良贷款率","不良贷款余额","拨备覆盖率","资本充足率","逾期贷款率",
    "员工人数","网点数量","个人客户数","对公客户数"
}


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "执行sql语句"})

    try:
        dw_mysql_repository = runtime.context["dw_mysql_repository"]
        query = state["query"]
        sql = state["sql"]
        perms = state.get("user_permissions", {})
        allowed_inds = perms.get("allowed_indicators", [])
        allowed_orgs = perms.get("allowed_orgs", [])
        is_admin = perms.get("is_admin", False)
        logger.info(f"权限上下文: pos={perms.get('position')}, admin={is_admin}, inds={len(allowed_inds)}, orgs={allowed_orgs}")

        # 1. 执行前:SQL 级白名单校验(第二层)
        #    从 SQL 精确提取 index_name='xxx' / org_name='xxx',与白名单做差集
        #    不再做行级子串扫描(会误杀合法列文本、漏网计算指标)
        if not is_admin and (allowed_inds or allowed_orgs):
            used_inds = set(_RE_INDS.findall(sql))
            if allowed_inds and used_inds:
                forbidden_inds = used_inds - set(allowed_inds)
                if forbidden_inds:
                    logger.warning(f"权限拦截: SQL 含越权指标 {forbidden_inds}")
                    writer({"result": [], "sql": sql, "perm_rejected": True,
                            "hint": "您无权查询该指标,已拦截。"})
                    await _write_audit(runtime, state, sql, [], True)
                    return {"sql": sql, "perm_rejected": True}

            used_orgs = set(_RE_ORGS.findall(sql))
            if allowed_orgs and used_orgs:
                allowed_orgs_full = set(f"江苏省{o}农商行" for o in allowed_orgs)
                forbidden_orgs = used_orgs - allowed_orgs_full
                if forbidden_orgs:
                    logger.warning(f"权限拦截: SQL 含越权机构 {forbidden_orgs}")
                    writer({"result": [], "sql": sql, "perm_rejected": True,
                            "hint": "您无权查询该机构数据,已拦截。"})
                    await _write_audit(runtime, state, sql, [], True)
                    return {"sql": sql, "perm_rejected": True}

        # 2. 执行SQL
        result = await dw_mysql_repository.execute_sql(sql)

        # 3. 去除None行
        if result:
            result = [r for r in result if any(v is not None for v in r.values())]
        has_data = bool(result)

        # 4. 无数据提示(收敛文案:不再混入权限措辞,权限拦截已在上面单独处理)
        missing_hint = ""
        if not has_data:
            missing_hint = "未查询到数据,请检查机构或指标是否正确。"

        # mixed 路由:外部结果徽标透传前端(数据已在 call_external_tool 并行取回)
        external = state.get("external_result") or []
        ext_badge = ""
        if external:
            sources = "、".join(dict.fromkeys(e.get("source", "") for e in external if e))
            ext_badge = sources
            writer({"external_source": sources})

        writer({"result": result, "sql": sql, "hint": missing_hint})
        elapsed = time.time() - state.get("start_time", time.time())
        logger.info(f"总耗时 {elapsed:.1f}s | 执行sql成功,结果：{result}")

        # 审计 + 经验日志(P0 自迭代采集)
        await _write_audit(runtime, state, sql, result, False)
        try:
            meta_repo = runtime.context["meta_mysql_repository"]
            await meta_repo.write_experience(
                query_text=query, final_sql=sql,
                outcome="corrected" if state.get("retry_count", 0) > 0 else "success",
                correction_path="correct" if state.get("retry_count", 0) > 0 else None,
                latency_ms=int(elapsed * 1000),
                user_position=perms.get("position", ""),
            )
        except Exception as e:
            logger.warning(f"经验日志写入失败(忽略): {e}")

        # 5. 排名全是1 → 触发correct_sql重生成
        if result and len(result) >= 2:
            rank_cols = [k for k in result[0].keys() if "排名" in str(k)]
            if rank_cols and all(r[rank_cols[0]] == 1 for r in result):
                logger.warning("检测到排名全部为1，触发correct_sql重生成")
                return {"error": "RANK在WHERE筛选之后执行，排名全为1。请改为先全量RANK再外层筛选", "sql": sql}

        # 6+7. 图表 + 报告并行(无依赖,各看 result,省一半 LLM 等待)
        if has_data:
            import asyncio as _aio

            async def _gen_chart():
                try:
                    tml = await loader_prompt("chart_config")
                    prompt = PromptTemplate(template=tml, input_variables=["query", "result"])
                    chain = prompt | llm | JsonOutputParser()
                    chart_config = await chain.ainvoke({
                        "query": query, "result": json.dumps(result, ensure_ascii=False)
                    })
                    writer({"chart": chart_config.get("chart", {})})
                except Exception as e:
                    logger.warning(f"图表生成失败: {e}")

            async def _gen_report():
                try:
                    from app.agent.tools.tool_executor import format_external_ctx
                    ext_ctx = format_external_ctx(external) if external else ""
                    tml = await loader_prompt("analysis_report")
                    if ext_ctx:
                        tml += "\n\n【外部参考】(与库内数据分别陈述,禁止因果结论)\n" + ext_ctx
                    prompt = PromptTemplate(template=tml, input_variables=["query", "result"])
                    chain = prompt | llm | StrOutputParser()
                    report = await chain.ainvoke({
                        "query": query, "result": json.dumps(result, ensure_ascii=False)
                    })
                    writer({"report": report.strip()})
                except Exception as e:
                    logger.warning(f"报告生成失败: {e}")

            await _aio.gather(_gen_chart(), _gen_report())

        return {}

    except Exception as e:
        logger.error(f"执行sql异常：{str(e)}")
        # 经验记录:失败案例(蒸馏规避规则的素材)
        try:
            meta_repo = runtime.context["meta_mysql_repository"]
            await meta_repo.write_experience(
                query_text=state.get("query", ""), final_sql=state.get("sql", ""),
                outcome="failed", error_message=str(e)[:500],
                latency_ms=int((time.time() - state.get("start_time", time.time())) * 1000),
                user_position=(state.get("user_permissions") or {}).get("position", ""),
            )
        except Exception as log_e:
            logger.warning(f"失败经验记录失败(忽略): {log_e}")
        raise


async def _write_audit(runtime: Runtime[DataAgentContext], state: DataAgentState,
                       sql: str, result: list, rejected: bool):
    """审计日志写入(独立逻辑,失败不影响主流程)"""
    log_id = state.get("log_id")
    if not log_id:
        return
    try:
        meta_repo = runtime.context["meta_mysql_repository"]
        await meta_repo.session.execute(text(
            "UPDATE query_log SET generated_sql=:s, result_data=:r, is_rejected=:p WHERE id=:i"
        ), {"s": sql,
            "r": json.dumps(result, ensure_ascii=False) if result else "[]",
            "p": 1 if rejected else 0,
            "i": log_id})
        await meta_repo.session.commit()
        logger.info(f"审计写入成功: log_id={log_id}, rejected={rejected}")
    except Exception as e:
        logger.warning(f"审计写入失败: {e}")
