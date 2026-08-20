"""Agent Loop 的五个工具。

层次化设计(2026-08-18):
- 知识查询工具(查定义):模型先识别模糊概念→调工具获取确定性定义
- 数据查询工具(查数据):用定义好的具体指标调数据源

- lookup_indicator_group  组词→具体指标列表(查 indicator_groups 表)
- lookup_formula          计算指标→公式模板+组件指标(查 indicator_formula 表)
- query_finance_db        行内指标值(13家农商行)→ 14 节点图
- query_macro_indicator   宏观/行业指标 → iFinD EDB
- search_financial_news    财经资讯/政策 → iFinD news
"""
import json
import re
from typing import Callable

from langchain_core.tools import tool
from sqlalchemy import text

from app.agent.context import DataAgentContext
from app.core.log import logger

# ---------- 工具0a:指标组查询(知识层) ----------

@tool
async def lookup_indicator_group(keyword: str = "", config=None) -> str:
    """列出系统已定义的行内指标组清单(组名+一句话描述)。
    用户提到可能对应一组指标的概念(如"效益""规模""质量""效率")时调用。
    看完清单后,用 read_knowledge 读取具体组的文件获取完整指标列表。
    """
    from pathlib import Path as _Path
    from app.config import ROOT_DIR
    kdir = ROOT_DIR / "knowledge" / "indicator_groups"
    if not kdir.exists():
        return json.dumps({"status": "not_found", "hint": "知识目录不存在"}, ensure_ascii=False)
    lines = []
    for f in sorted(kdir.glob("*.md")):
        # 从文件首行取组名,第二段取描述
        text = f.read_text(encoding="utf-8")
        first = text.split("\n", 1)[0].lstrip("# ").strip()
        desc = ""
        for ln in text.split("\n"):
            if ln.startswith("**描述**"):
                desc = ln.replace("**描述**:", "").strip()
                break
        lines.append(f"- {first}: {desc} (读文件: {f.stem})")
    return json.dumps({"status": "ok", "groups": lines,
                       "hint": "调用 read_knowledge(filename) 读取某组的完整指标定义"},
                      ensure_ascii=False)


@tool
async def read_knowledge(filename: str, config=None) -> str:
    """读取知识文件内容。filename 为知识清单中列出的文件名(不含路径,如 '盈利能力')。
    返回该知识文件的完整内容(指标组则包含全部具体指标名)。"""
    from app.config import ROOT_DIR
    # 只允许 knowledge/ 目录下,防路径穿越
    safe = filename.replace("\\", "/").split("/")[-1].removesuffix(".md")
    base = ROOT_DIR / "knowledge"
    candidates = list(base.rglob(f"{safe}.md"))
    if not candidates:
        return json.dumps({"status": "not_found",
                           "hint": f"未找到知识文件'{filename}',先调 lookup_indicator_group 看清单"},
                          ensure_ascii=False)
    text = candidates[0].read_text(encoding="utf-8")
    return json.dumps({"status": "ok", "file": candidates[0].name, "content": text},
                      ensure_ascii=False)


# ---------- 工具0b:计算指标公式查询(知识层) ----------

@tool
async def lookup_formula(keyword: str, config=None) -> str:
    """查询计算型指标公式定义。当用户提到的指标可能是由多个基础指标
    计算得出的（如"存贷比""人均利润""点均存款""拨贷率""营业利润率"等）时调用。
    返回计算口径说明、SQL计算模板和组件指标名。
    如果关键词不匹配任何公式,返回未找到提示。

    请求样例:
    {"keyword":"存贷比"}
    {"keyword":"人均存款"}"""
    ctx = _get_ctx(config)
    repo = ctx["meta_mysql_repository"]
    rows = (await repo.session.execute(text(
        "SELECT term, aliases, formula_type, index_names, sql_template, description "
        "FROM indicator_formula"
    ))).fetchall()
    import json as _json
    results = []
    for r in rows:
        aliases = []
        try:
            aliases = _json.loads(r.aliases) if r.aliases else []
        except Exception:
            pass
        index_names = r.index_names
        if isinstance(index_names, str):
            try:
                index_names = _json.loads(index_names)
            except Exception:
                index_names = []
        # 模糊匹配:关键词命中 term、别名、描述、或组件指标名
        candidates = [r.term] + aliases
        if (any(keyword in c or c in keyword for c in candidates)
            or (r.description and keyword in r.description)
            or any(keyword in str(ind) for ind in (index_names or []))):
            results.append({
                "term": r.term, "aliases": aliases,
                "description": r.description or "",
                "sql_template": r.sql_template or "",
                "index_names": index_names or [],
            })
    if not results:
        return json.dumps({"status": "not_found",
                           "hint": f"未找到与'{keyword}'匹配的计算公式,可能是直接指标,直接用 query_finance_db 查询"},
                          ensure_ascii=False)
    return json.dumps({"status": "ok", "formulas": results}, ensure_ascii=False)


# ---------- 工具1:行内问数(数据层) ----------

_finance_cache: dict = {"result": None, "used": False}

def reset_finance_cache():
    _finance_cache["result"] = None
    _finance_cache["used"] = False

@tool
async def query_finance_db(question: str, config=None) -> str:
    """江苏省13家农商行(A市~M市农商行)行内经营指标数据库查询工具。
    查询需含:机构名+指标名+日期,均可从对话历史继承。
    数据区间2024-12-31至2026-04-30。
    用户问"变化/走势/趋势"时,按日查区间内全部日期,不要只查月末。
    用户说"13家/所有/各家"时,一次查全部机构同一日期。
    指标口径模糊(如"贷款"未指明对公/个人)时返回need_clarify及候选项。

    请求样例:
    {"question":"A市农商行2025年6月末不良贷款率是多少"}
    {"question":"对比A市和B市农商行2025年净利润"}
    {"question":"E市农商行2026年3月末存贷比是多少"}"""
    from app.agent.agent_runner import run_finance_pipeline
    if _finance_cache["used"]:
        return _finance_cache["result"]
    result = await run_finance_pipeline(question, _get_ctx(config), _get_writer(config))
    _finance_cache["result"] = result
    _finance_cache["used"] = True
    return result


# ---------- 工具2:宏观指标(数据层) ----------

@tool
async def query_macro_indicator(query: str, config=None) -> str:
    """宏观及行业经济指标数据查询工具(国家统计局口径,经 iFinD EDB)。
    覆盖:CPI/PPI/LPR/PMI/GDP/社融/金价/油价/行业产销量等行外经济数据。
    query 规范:"标准指标名+时间",如 "CPI当月同比 2025年3月"。
    通胀类默认用当月同比;用户明确说"定基指数"才查指数。严禁包含银行机构名。

    请求样例:
    {"query":"CPI当月同比 2025年1月-2025年12月"}
    {"query":"江苏省GDP 2025年"}"""
    ctx = _get_ctx(config)
    from app.agent.tools.tool_executor import ExternalToolExecutor
    executor = ExternalToolExecutor()
    result = await executor.query_edb(query)
    if not result:
        return json.dumps({"status": "no_data", "note": "EDB 未返回数据,建议更换指标名或时间范围"},
                          ensure_ascii=False)
    return json.dumps({"status": "ok", "source": result.get("source", ""),
                       "markdown": result.get("markdown", "")[:2000],
                       "rows": result.get("rows", [])[:30]}, ensure_ascii=False)


# ---------- 工具3:财经资讯(数据层) ----------

@tool
async def search_financial_news(query: str, time_start: str = "", time_end: str = "", config=None) -> str:
    """同花顺财经新闻资讯检索。适用:地产政策/央行货政/行业动态/市场热点。
    query 用核心关键词;time_start/time_end 格式 YYYY-MM-DD,默认近一年。

    请求样例:
    {"query":"房地产政策 保交楼 2025年","time_start":"2025-01-01","time_end":"2025-12-31"}"""
    ctx = _get_ctx(config)
    from app.agent.tools.tool_executor import ExternalToolExecutor
    executor = ExternalToolExecutor()
    result = await executor.query_news(query, time_start or None, time_end or None)
    if not result:
        return json.dumps({"status": "no_data", "note": "资讯源未返回结果,建议更换关键词"},
                          ensure_ascii=False)
    return json.dumps({"status": "ok", "source": result.get("source", ""),
                       "items": result.get("rows", [])[:5]}, ensure_ascii=False)


# ---------- 运行期注入 ----------

def _get_ctx(config) -> DataAgentContext:
    if config is None:
        raise RuntimeError("工具上下文未注入")
    return config["ctx"]

def _get_writer(config) -> Callable[[dict], None]:
    if config is None:
        raise RuntimeError("工具上下文未注入")
    return config["writer"]


ALL_TOOLS = [lookup_indicator_group, read_knowledge, lookup_formula,
             query_finance_db, query_macro_indicator, search_financial_news]
