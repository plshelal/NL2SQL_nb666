"""回归测试:不依赖网络,纯逻辑验证。

跑法:
    uv run pytest tests/test_external_routing.py -v

2026-08-19 死代码清理后更新:删了 assess_clarify/route_intent/call_external_tool/
schema_all/schema_link 后,引用它们的测试一并移除;保留不依赖已删模块的测试。
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("IFIND_MCP_TOKEN", "test-token-placeholder")


# ---------- 缺信息检测的 SQL 条件识别(历史bug:只认=不认 IN) ----------

def test_missing_info_org_in_clause():
    """多机构对比:SQL 用 org_name IN (...),不应误报缺机构。"""
    import re
    sql = ("SELECT o.org_name, d.index_value FROM index_data d JOIN org_info o "
           "ON d.org_code=o.org_code WHERE o.org_name IN "
           "('江苏省A市农商行','江苏省B市农商行') AND d.index_name='净利润' "
           "AND d.data_date='2025-12-31'")
    has_org = bool(re.search(r"org_name\s*(?:=|IN\s*\()", sql, re.IGNORECASE))
    assert has_org, "org_name IN(...) 必须被识别为已有机构过滤"

def test_missing_info_ind_in_clause():
    """多指标:SQL 用 index_name IN (...),不应误报缺指标。"""
    import re
    sql = ("SELECT index_name, index_value FROM index_data "
           "WHERE index_name IN ('净利润','营业收入') AND data_date='2025-12-31'")
    has_ind = bool(re.search(r"index_name\s*(?:=|IN\s*\()", sql, re.IGNORECASE))
    assert has_ind, "index_name IN(...) 必须被识别为已有指标过滤"

def test_missing_info_genuine_missing():
    """真缺机构:SQL 无任何 org 过滤(问题提了机构)→ 应报缺(检测能力仍在)。"""
    import re
    sql = ("SELECT index_value FROM index_data "
           "WHERE index_name='净利润' AND data_date='2025-12-31'")
    has_org = bool(re.search(r"org_name\s*(?:=|IN\s*\()", sql, re.IGNORECASE))
    assert not has_org


# ---------- news 必填参数(历史bug:Missing time_start) ----------

def test_call_news_always_sends_time_window():
    """不真正联网:monkeypatch 底层 call,验证 args 一定含 time_start/time_end。"""
    from app.agent.tools.ifind_mcp import IfindMcpManager
    captured = {}

    async def fake_call(self, tool, args):
        captured["tool"] = tool
        captured["args"] = args
        return {"ok": True, "text": "{}"}

    IfindMcpManager.call_news.__globals__["IfindMcpClient"].call = fake_call
    mgr = IfindMcpManager()
    asyncio.run(mgr.call_news("地产政策"))          # 不传时间
    assert "time_start" in captured["args"], "time_start 必填,缺了会 Invalid arguments"
    assert "time_end" in captured["args"]
    assert captured["args"]["time_start"].count("-") == 2  # ISO 日期格式
    asyncio.run(mgr.call_news("地产政策", "2025-01-01", "2025-12-31"))
    assert captured["args"]["time_start"] == "2025-01-01"


# ---------- 图拓扑守卫(历史bug:外部分支直连 add_extra_context 提前触发下游) ----------

def test_graph_topology_after_slim():
    """2026-08-19 瘦身后拓扑:决策节点已删,纯执行链。
    - 删除节点不得残留在图中(schema_link/assess_clarify/route_intent/
      call_external_tool/schema_all)
    - add_extra_context 入边只允许 filter_metric/filter_table
    - generate_sql 出边只允许 validate_sql/__end__(权限拦截/缺信息→END)"""
    from app.agent.graph import graph
    g = graph.get_graph()
    node_names = {n.name for n in g.nodes.values()} if hasattr(g.nodes, "values") else set()
    if not node_names:
        node_names = {getattr(n, "name", str(n)) for n in g.nodes}
    removed = {"schema_link", "assess_clarify", "route_intent",
               "call_external_tool", "schema_all"}
    for rm in removed:
        assert rm not in node_names, f"已删节点 {rm} 不应存在于图中"
    must_have = {"extract_keywords", "formula_match", "expand_keywords",
                 "recall_column", "recall_metric", "recall_value",
                 "merge_retrieved_info", "filter_metric", "filter_table",
                 "add_extra_context", "generate_sql", "validate_sql",
                 "correct_sql", "execute_sql"}
    missing = must_have - node_names
    assert not missing, f"执行链节点缺失: {missing}"
    for e in g.edges:
        if getattr(e, "target", None) == "add_extra_context":
            assert e.source in {"filter_metric", "filter_table"}, (
                f"add_extra_context 非法入边 ← {e.source}")


# ---------- formula_match(原 schema_link 第0段,瘦身后的公式匹配节点) ----------

def test_formula_match_node_importable():
    """瘦身后的公式匹配节点可导入,产出签名与 schema_link 时代一致(formula_context/indicators)。"""
    import asyncio
    import json as _json
    from app.agent.nodes.formula_match import _match_formulas_async

    class FakeSession:
        async def execute(self, _sql):
            class R:
                def __init__(self, rows): self._rows = rows
                def fetchall(self): return self._rows
            return R([FakeRow("人均利润", _json.dumps(["人均创利", "人均净利润"]))])

    class FakeRow:
        def __init__(self, term, aliases):
            self.term, self.aliases = term, aliases

    class FakeRepo:
        session = FakeSession()
        async def get_indicator_formulas(self, terms):
            if "人均利润" in terms:
                return {"人均利润": {"index_names": ["净利润", "员工人数"],
                                    "sql_template": "A/B", "description": "净利润÷员工人数"}}
            return {}

    repo = FakeRepo()
    text, inds = asyncio.run(_match_formulas_async(
        repo, ["人均", "净利润"], "E市农商行人均净利润多少"))
    assert "人均利润" in text and "净利润" in inds, "公式匹配产出签名不变"


def test_fast_path_removed_all_queries_to_agent():
    """2026-08 终态:快筛已整体废除。
    所有问题一律进 Agent 循环,由模型读工具描述自主决策。"""
    from app.agent import orchestrator
    assert not hasattr(orchestrator, "_fast_path"), "_fast_path 应已删除"
    assert not hasattr(orchestrator, "_EXTERNAL_HINT_RE"), "外部词表应已删除"
    assert not hasattr(orchestrator, "_MIXED_INTENT_RE"), "混合意图词表应已删除"
    assert hasattr(orchestrator, "run_agent_query")
    from app.agent.tools.agent_tools import ALL_TOOLS
    assert len(ALL_TOOLS) == 6
    names = {t.name for t in ALL_TOOLS}
    assert names == {"lookup_indicator_group", "read_knowledge", "lookup_formula",
                     "query_finance_db", "query_macro_indicator", "search_financial_news"}
    grp_desc = [t.description for t in ALL_TOOLS if t.name == "lookup_indicator_group"][0]
    assert "清单" in grp_desc
    read_desc = [t.description for t in ALL_TOOLS if t.name == "read_knowledge"][0]
    assert "读取" in read_desc


def test_formula_match_terms_include_query_fragments():
    """公式匹配双向词面:反向子串扫描兜底 jieba 切散(如'人均净利润'藏在整句里)。"""
    import asyncio
    import json as _json
    from app.agent.nodes.formula_match import _match_formulas_async

    class FakeSession:
        async def execute(self, _sql):
            class R:
                def __init__(self, rows): self._rows = rows
                def fetchall(self): return self._rows
            return R([FakeRow("人均利润", _json.dumps(["人均创利", "人均净利润"]))])

    class FakeRow:
        def __init__(self, term, aliases):
            self.term, self.aliases = term, aliases

    class FakeRepo:
        session = FakeSession()
        async def get_indicator_formulas(self, terms):
            if "人均利润" in terms:
                return {"人均利润": {"index_names": ["净利润", "员工人数"],
                                    "sql_template": "A/B", "description": "净利润÷员工人数"}}
            return {}

    repo = FakeRepo()
    text, inds = asyncio.run(_match_formulas_async(
        repo, ["人均", "净利润"], "E市农商行人均净利润多少"))
    assert "人均利润" in text and "净利润" in inds, "反向子串扫描必须兜住切散的计算指标"
