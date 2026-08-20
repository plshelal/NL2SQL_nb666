"""Agent 编排层(2026-08-18 层次化设计)。

层次化:知识查询工具(查定义) + 数据查询工具(查数据)
模型先识别模糊概念→调知识工具获取确定性定义→用具体指标调数据工具

五个工具:
- lookup_indicator_group  组词→具体指标(知识层,查 indicator_groups)
- lookup_formula          计算指标→公式模板(知识层,查 indicator_formula)
- query_finance_db        行内指标值(数据层,14节点图)
- query_macro_indicator   宏观数据(数据层,iFinD EDB)
- search_financial_news   财经资讯(数据层,iFinD news)
"""
import json
import re
import time

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.tools.agent_tools import (ALL_TOOLS,
                                         lookup_indicator_group,
                                         read_knowledge,
                                         lookup_formula,
                                         query_finance_db,
                                         query_macro_indicator,
                                         search_financial_news,
                                         reset_finance_cache)
from app.core.log import logger

SYSTEM_PROMPT = """你是银行金融问数助手,服务对象是江苏省13家农商行(A市~M市农商行)的员工。

【可用工具】(根据问题自主决定调用哪一个/哪几个)
知识查询工具(先查定义,再查数据):
1. lookup_indicator_group — 列出系统定义的指标组清单(组名+描述)。用户说"效益/规模/质量/效率"等模糊维度词时先调它看清单。
2. read_knowledge — 读取知识文件全文。看完组清单后,读语义最接近的组文件获取完整指标列表(如"效益"最接近"盈利能力"就读它)。
3. lookup_formula — 查计算指标公式。用户说"存贷比/人均利润/点均存款"等可能是计算指标时调用,返回公式模板和组件指标。

数据查询工具(用定义好的具体指标查数据):
4. query_finance_db — 行内数据库:13家农商行经营指标。查询需含 机构名+指标名+日期,均可从对话历史继承。数据区间2024-12-31至2026-04-30。
5. query_macro_indicator — 宏观/行业经济数据(国家统计局):CPI/PPI/LPR/PMI/GDP/社融/金价/油价。问行外经济数据用它。
6. search_financial_news — 财经资讯/政策/新闻/公告检索。

【行为总则】(优先级最高,先于一切工具调用执行)
1. 上下文优先:对话历史中已明确出现的信息,直接沿用,不追问。
   "上下文"仅指历史或本轮明示的要素,不含你自己推断出的值。
2. 调工具前先确认输入:每个工具描述里写了它需要什么输入。调用前检查用户是否提供了
   工具所需的必要信息(或可从历史继承)。缺失的不得编造、不得用默认值填充,先反问用户。
   反问规范:
   - 一次只问 1 个最关键的缺失项;选项每行一个,以"- "开头且为完整可执行的查询语句;
   - 最多澄清 2 轮;用户仍无法明确时,停止追问,说明难以准确理解,并给最佳默认查询。
3. 多义必澄清:指标/指代存在多种合理解释且影响结果时(如"A行咋样"——效益?不良?规模?),
   不得猜测作答,先问用户。
4. 信息充足直接答:工具所需输入齐全(或可继承)时立即执行,禁止无意义反问、禁止为问而问。
5. 禁止编造:任何要素既不在历史也不在本轮明示时,严禁用默认值填充,一律按第 2/3 条反问。

【查询规则】
- 工具选择:行内→query_finance_db;外部数值→query_macro_indicator;政策/资讯→search_financial_news;
  内外结合→对应工具各调一次,分述内外部事实、注明来源、只说时间吻合,严禁下因果结论。
- query_finance_db 每个用户问题只调用一次;拿到完整数据直接回答,不重复调用。
- 宽泛外部需求("经济形势/宏观情况")→ 并行查多个代表性指标(GDP同比/CPI当月同比/PMI/工业增加值/社融),不要只查一个。
- 日期粒度:问"变化/走势/趋势"→ 查区间内逐日(如4月→4月1日至30日,30条),不得只查月末;用户指定日期→ 按指定;都没说→ 按行为总则2反问,不默认。
- 碎片补全:用户说"每天的""换B市""第一种"等指代上一轮时,结合历史补全为完整自包含问题再调工具;
  补全只允许继承"历史已出现"或"本轮明示"的要素,其余缺失即反问(行为总则2)。严禁把碎片原文直接传给工具。
- 工具返回 need_clarify / missing_info → 原样转述给用户补充,不要自行猜测。
- "全省均值/行业平均/和其他行比"→ 查全部13家农商行,模型自己算平均。

【闲聊处理】
- 观点/知识/寒暄类(如"银行获客难点在哪""什么是拨备覆盖率""你好"):不调用任何工具,直接回答。

【回答格式】
- 调用了 query_finance_db 且获得数据时,必须用 markdown 表格展示数据(表头+分隔行+全部数据行,超过30行取前30),不得只写摘要。
- 关键结论用**加粗**;叙述用短段落或"- "列表,不要整段长文;不要输出 HTML 标签。
- 外部数据注明来源(如"来源:国家统计局");不编造数据,工具返回 no_data 就明说。
- 中文回答,先给数据,再给不超过150字的简析。"""


def _build_input_messages(question: str, chat_context: dict | None) -> list[HumanMessage]:
    """harness 式输入构建:对话历史 + 当前问题。"""
    msgs: list[HumanMessage] = []
    hist = ((chat_context or {}).get("history")) or []
    last = (chat_context or {}).get("last_turn")
    if hist or last:
        lines = []
        for turn in hist[-3:]:
            q = (turn or {}).get("question", "")
            a = str((turn or {}).get("answer", ""))
            if q:
                lines.append(f"用户: {q}")
                if a:
                    lines.append(f"系统: {a}")
        if last and (not hist or (last.get("question") != hist[-1].get("question"))):
            lines.append(f"用户: {last.get('question', '')}")
            a = str(last.get("answer", ""))
            if a:
                lines.append(f"系统: {a}")
        if lines:
            msgs.append(HumanMessage(
                content="【对话历史】(当前问题可能是指代其中的内容,按意图补全规则处理)\n"
                        + "\n".join(lines)))
    msgs.append(HumanMessage(content=question))
    return msgs


async def run_agent_query(question: str, ctx: DataAgentContext, writer,
                          user_permissions: dict | None = None,
                          chat_context: dict | None = None, log_id: int | None = None):
    """主入口:SSE 事件生成器。所有问题 → Agent 循环,模型自主选工具。"""
    from app.agent.agent_runner import run_finance_pipeline

    reset_finance_cache()
    logger.info("[orchestrator] Agent 循环(全量)")
    writer({"stage": "分析数据源"})
    t0 = time.time()

    # 行内工具包装:附加用户权限/对话上下文/审计id
    async def _fin(question: str, config=None):
        return await run_finance_pipeline(
            question, ctx, writer, user_permissions, chat_context, log_id)

    fin_tool = StructuredTool.from_function(
        coroutine=_fin,
        name=query_finance_db.name,
        description=query_finance_db.description,
        args_schema=query_finance_db.args_schema,
    )

    # 知识查询工具 + 外部工具闭包绑定 ctx
    class _EvtWriter:
        def __call__(self, chunk: dict):
            writer(chunk)

    # 知识层工具:组清单/读文件/公式(文件制——模型看清单自己读文件,不搬运内容进prompt)
    grp_tool = StructuredTool.from_function(
        coroutine=lambda **kw: _run_lookup(lookup_indicator_group, kw, ctx, writer),
        name=lookup_indicator_group.name,
        description=lookup_indicator_group.description,
        args_schema=lookup_indicator_group.args_schema,
    )
    read_tool = StructuredTool.from_function(
        coroutine=lambda **kw: _run_lookup(read_knowledge, kw, ctx, writer),
        name=read_knowledge.name,
        description=read_knowledge.description,
        args_schema=read_knowledge.args_schema,
    )
    fml_tool = StructuredTool.from_function(
        coroutine=lambda **kw: _run_lookup(lookup_formula, kw, ctx, writer),
        name=lookup_formula.name,
        description=lookup_formula.description,
        args_schema=lookup_formula.args_schema,
    )
    ext_macro = _bind_ext(query_macro_indicator, ctx, _EvtWriter())
    ext_news = _bind_ext(search_financial_news, ctx, _EvtWriter())

    # RubikSQL 回灌:approved 的 tool_choice/alias 规则注入系统提示(异步,独立 session+缓存)
    from app.agent.knowledge_feedback import get_prompt_block, get_semantic_hints
    prompt_block = await get_prompt_block()
    semantic_hints = await get_semantic_hints(question)
    agent = create_react_agent(llm, [grp_tool, read_tool, fml_tool, fin_tool, ext_macro, ext_news],
                               prompt=SYSTEM_PROMPT + prompt_block + semantic_hints)

    tool_trace: list[str] = []
    final_text = ""
    try:
        # 单次 ainvoke(不跑两次):思考流从 messages 后处理推送
        # 之前 astream+ainvoke 两阶段导致 Agent 循环跑两次,行为不一致(一轮查数据一轮反问)
        result = await agent.ainvoke({"messages": _build_input_messages(question, chat_context)})
        msgs = result.get("messages", [])
        for m in msgs:
            t = m.__class__.__name__
            if t == "AIMessage":
                content = str(getattr(m, "content", "")).strip()
                if content and len(content) > 5:
                    writer({"stage": f"💭 {content[:300]}"})
                if getattr(m, "tool_calls", None):
                    for tc in m.tool_calls:
                        nm = tc.get("name", "")
                        args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
                        short_args = args_str if len(args_str) <= 100 else args_str[:100] + "..."
                        writer({"stage": f"🔧 调用工具:{nm}({short_args})"})
                        tool_trace.append(f"{nm}({args_str[:150]})")
            elif t == "ToolMessage":
                tool_name = getattr(m, "name", "unknown")
                content_head = str(getattr(m, "content", ""))[:80]
                ok_flag = '"status": "ok"' in content_head
                status = "✅ 成功" if ok_flag else "⚠️ 无数据"
                writer({"stage": f"📡 {tool_name} 返回:{status}"})
                tool_trace.append(f"  -> {'ok' if ok_flag else 'no_data'} {content_head[:60]}")
        # 最终回答:最后一条无 tool_calls 的 AIMessage
        for m in reversed(msgs):
            if m.__class__.__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
                final_text = m.content if m.content else ""
                break

    except Exception as e:
        logger.error(f"[orchestrator] Agent 循环异常: {e}")
        final_text = f"查询过程出错: {e}"

    logger.info(f"[orchestrator] 最终回答({len(tool_trace)}次工具调用): {str(final_text)[:500]}")

    # RubikSQL:Agent 轨迹经验
    try:
        await ctx["meta_mysql_repository"].write_experience(
            query_text=question,
            final_sql="",
            outcome="agent_trace",
            error_message=" || ".join(tool_trace)[:1000] or final_text[:500],
            latency_ms=int((time.time() - t0) * 1000),
            user_position=(user_permissions or {}).get("position", ""),
        )
    except Exception as e:
        logger.warning(f"[orchestrator] agent_trace 经验写入失败(忽略): {e}")

    # 工具调用链同步到 query_log(审核员在待审卡片直接看到 Agent 调了哪些工具)
    if log_id and tool_trace:
        try:
            from sqlalchemy import text as _t
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            async with meta_mysql_client_manager.session_factory() as _s:
                await _s.execute(_t(
                    "UPDATE query_log SET tool_trace=:t WHERE id=:i"),
                    {"t": " || ".join(tool_trace)[:2000], "i": log_id})
                await _s.commit()
        except Exception as e:
            logger.warning(f"[orchestrator] tool_trace 写 query_log 失败(忽略): {e}")

    # 回写最终回答到 query_log.result_summary(审核员据此复核语义对错)
    if log_id is not None:
        try:
            from sqlalchemy import text as _t
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            async with meta_mysql_client_manager.session_factory() as _s:
                await _s.execute(_t(
                    "UPDATE query_log SET result_summary=:r WHERE id=:i"),
                    {"r": str(final_text)[:5000], "i": log_id})
                await _s.commit()
        except Exception as e:
            logger.warning(f"[orchestrator] result_summary 回写失败(忽略): {e}")

    writer({"final_answer": final_text})
    return


async def _run_lookup(tool_obj, kwargs, ctx, writer):
    """运行知识查询工具(文件制):组清单/读文件/公式查表。"""
    kwargs.pop("config", None)
    from app.config import ROOT_DIR

    if tool_obj is lookup_indicator_group:
        # 列清单:knowledge/indicator_groups/*.md 的组名+描述
        kdir = ROOT_DIR / "knowledge" / "indicator_groups"
        if not kdir.exists():
            return json.dumps({"status": "not_found", "hint": "知识目录不存在"}, ensure_ascii=False)
        lines = []
        for f in sorted(kdir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            first = text.split("\n", 1)[0].lstrip("# ").strip()
            desc = ""
            for ln in text.split("\n"):
                if ln.startswith("**描述**"):
                    desc = ln.replace("**描述**:", "").strip()
                    break
            lines.append(f"- {first}: {desc} (read_knowledge filename='{f.stem}')")
        return json.dumps({"status": "ok", "groups": lines}, ensure_ascii=False)

    elif tool_obj is read_knowledge:
        # 读知识文件(防路径穿越:只允许 knowledge/ 下的文件名)
        filename = kwargs.get("filename", "")
        safe = filename.replace("\\", "/").split("/")[-1].removesuffix(".md")
        base = ROOT_DIR / "knowledge"
        candidates = list(base.rglob(f"{safe}.md"))
        if not candidates:
            return json.dumps({"status": "not_found",
                               "hint": f"未找到知识文件'{filename}'"}, ensure_ascii=False)
        text = candidates[0].read_text(encoding="utf-8")
        return json.dumps({"status": "ok", "file": candidates[0].name, "content": text},
                          ensure_ascii=False)

    else:  # lookup_formula — 查 indicator_formula 表(DB 表,由 lifespan seed)
        keyword = kwargs.get("keyword", "")
        from sqlalchemy import text as _t
        from app.clients.mysql_client_manager import meta_mysql_client_manager
        async with meta_mysql_client_manager.session_factory() as s:
            rows = (await s.execute(_t(
                "SELECT term, aliases, index_names, sql_template, description FROM indicator_formula"
            ))).fetchall()
        import json as _j
        results = []
        for r in rows:
            aliases = []
            try:
                aliases = _j.loads(r.aliases) if r.aliases else []
            except Exception:
                pass
            idx_names = r.index_names
            if isinstance(idx_names, str):
                try:
                    idx_names = _j.loads(idx_names)
                except Exception:
                    idx_names = []
            cands = [r.term] + aliases
            if (any(keyword in c or c in keyword for c in cands)
                or (r.description and keyword in r.description)
                or any(keyword in str(ind) for ind in (idx_names or []))):
                results.append({"term": r.term, "aliases": aliases,
                                "description": r.description or "",
                                "sql_template": r.sql_template or "",
                                "index_names": idx_names or []})
        if not results:
            return json.dumps({"status": "not_found",
                               "hint": f"未找到与'{keyword}'匹配的计算公式,可能是直接指标,直接用 query_finance_db 查询"},
                              ensure_ascii=False)
        return json.dumps({"status": "ok", "formulas": results}, ensure_ascii=False)


def _bind_ext(tool_obj, ctx, writer):
    from langchain_core.tools import StructuredTool
    orig = tool_obj
    async def _run(**kwargs):
        kwargs.pop("config", None)
        if orig is query_macro_indicator:
            return await _macro_impl(kwargs.get("query", ""), ctx, writer)
        return await _news_impl(kwargs.get("query", ""), kwargs.get("time_start") or None,
                                kwargs.get("time_end") or None, ctx, writer)
    return StructuredTool.from_function(
        coroutine=_run, name=orig.name, description=orig.description,
        args_schema=orig.args_schema)


async def _macro_impl(query, ctx, writer):
    from app.agent.tools.tool_executor import ExternalToolExecutor
    executor = ExternalToolExecutor()
    result = await executor.query_edb(query)
    await _audit_ext(ctx, "query_macro_indicator", query, bool(result))
    if not result:
        return json.dumps({"status": "no_data", "note": "EDB 未返回数据,建议更换指标名或时间范围"},
                          ensure_ascii=False)
    return json.dumps({"status": "ok", "source": result.get("source", ""),
                       "markdown": result.get("markdown", "")[:2000],
                       "rows": result.get("rows", [])[:30]}, ensure_ascii=False)


async def _news_impl(query, ts, te, ctx, writer):
    from app.agent.tools.tool_executor import ExternalToolExecutor
    executor = ExternalToolExecutor()
    result = await executor.query_news(query, ts, te)
    await _audit_ext(ctx, "search_financial_news", query, bool(result))
    if not result:
        return json.dumps({"status": "no_data", "note": "资讯源未返回结果"}, ensure_ascii=False)
    return json.dumps({"status": "ok", "source": result.get("source", ""),
                       "items": result.get("rows", [])[:5]}, ensure_ascii=False)


async def _audit_ext(ctx, tool_name: str, query: str, ok: bool):
    try:
        from sqlalchemy import text as _t
        from app.clients.mysql_client_manager import meta_mysql_client_manager
        async with meta_mysql_client_manager.session_factory() as s:
            await s.execute(_t(
                "INSERT INTO query_log (username, query_text, generated_sql, result_status) "
                "VALUES (:u,:q,:t,:s)"),
                {"u": "agent", "q": query, "t": f"[{tool_name}] {query}",
                 "s": "external_ok" if ok else "external_fail"})
            await s.commit()
    except Exception as e:
        logger.warning(f"[orchestrator] 外部审计失败(忽略): {e}")


# RubikSQL 回灌已迁移至 app.agent.knowledge_feedback(异步 + 独立 session + 缓存)。
# 原 _load_dynamic_rules/_augment_system_prompt 用 _aio.run() 但永远在异步上下文被调,
# get_running_loop 成功导致规则恒空——已删,改用 get_prompt_block() 在 run_agent_query 内 await。
