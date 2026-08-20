"""formula_match:计算指标模板匹配(纯执行层,零 LLM)。

源自 schema_link 三段瀑布的第0段(2026-08-18 内部图瘦身:
组词段/消歧/路由已删——决策职责归外层 Agent,内部只做确定性转换)。

双向词面匹配:
① 正向:分词结果查 indicator_formula 表(term/aliases 精确)
② 反向:全表 term+aliases 反查"是否为问题子串"(兜底 jieba 切散,
   如"人均净利润"藏在整句里)
产出 formula_context(模板文本) + formula_indicators(组件指标),
generate_sql 直接消费;generate_sql 的权限合并逻辑原样依赖本节点产物。
"""
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def _match_formulas_async(meta_repo, keywords: list, query: str) -> tuple[str, list]:
    import json
    from sqlalchemy import text as _t
    formulas = {}
    # ① 正向:分词查表
    kws = [k for k in (keywords or []) if len(k) >= 2]
    if kws:
        formulas.update(await meta_repo.get_indicator_formulas(kws))
    # ② 反向:全表扫描 term/aliases 是否为问题子串
    try:
        rows = (await meta_repo.session.execute(_t(
            "SELECT term, aliases FROM indicator_formula"))).fetchall()
        for r in rows:
            if r.term in formulas:
                continue
            candidates = [r.term]
            try:
                candidates += json.loads(r.aliases) if r.aliases else []
            except Exception:
                pass
            if any(len(c) >= 2 and c in query for c in candidates):
                one = await meta_repo.get_indicator_formulas([r.term])
                formulas.update(one)
    except Exception as e:
        logger.warning(f"[formula_match] 反向扫描失败(仅正向生效): {e}")

    if not formulas:
        return "", []
    import json as _json
    items = []
    formula_indicators: list = []
    for term, f in formulas.items():
        # MySQL JSON 列返回的是字符串,必须解析成 list,否则迭代逐字符
        comps = f.get("index_names") or []
        if isinstance(comps, str):
            try:
                comps = _json.loads(comps)
            except Exception:
                comps = []
        items.append(f"- {term}: {f['description']}")
        if comps:
            items.append(f"  组件指标: {'、'.join(comps)}")
        formula_indicators.extend(comps)
    formula_text = ("【已知计算公式(仅给口径,不给固定SQL——根据问法自由组装)】\n"
                     + "\n".join(items)
                     + "\n注意:index_data 是长表(每指标一行 index_value),计算时需用 "
                     "SUM(CASE WHEN index_name='组件指标' THEN index_value END) 透视。"
                     "根据问法自行组装:查值→WHERE过滤+计算;排名→子查询RANK;对比→全量+均值;"
                     "趋势→逐日。WHERE 必须含 org_name 和 data_date。")
    logger.info(f"[formula_match] 公式命中: {list(formulas.keys())}")
    logger.info(f"[formula_match] 公式命中: {list(formulas.keys())}")
    return formula_text, formula_indicators


async def formula_match(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "公式匹配"})
    meta_repo = runtime.context["meta_mysql_repository"]
    query = state["query"]
    keywords = state.get("keywords") or []

    formula_context, formula_indicators = await _match_formulas_async(meta_repo, keywords, query)
    return {
        "formula_context": formula_context,
        "formula_indicators": formula_indicators,
        "link_type": "computed" if formula_context else "normal",
        "linked_indicators": [],
    }
