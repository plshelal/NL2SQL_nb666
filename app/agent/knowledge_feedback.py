"""RubikSQL 知识反馈(读侧):approved 规则注入各消费者。

三类 approved 规则分别注入:
- tool_choice / alias → Agent 系统提示(orchestrator),引导工具选择与术语映射
- avoidance          → generate_sql 系统提示,规避历史错误 SQL 模式

统一独立 session(meta_mysql_client_manager.session_factory,避免与 agent_runner
共享 session 的 asyncmy readexactly 并发冲突)+ 10 分钟缓存。
原 orchestrator._load_dynamic_rules 用 _aio.run() 但永远在异步上下文被调,
get_running_loop 成功导致 _aio.run 分支跳过、规则恒空——此处修复为全异步。
"""
import json
import time

from app.core.log import logger

_cache = {"prompt": None, "avoid": None, "ts": 0.0}
_TTL = 600  # 10 分钟


async def _load_all() -> tuple[list[dict], list[dict]]:
    """独立 session 读取 approved 规则。

    返回 (prompt_rules, avoid_rules):
      prompt_rules: type∈{tool_choice, alias} → 注入 Agent 系统提示
      avoid_rules:  type=avoidance → 注入 generate_sql
    失败降级为空列表,不抛异常(不影响主流程)。
    """
    now = time.time()
    if _cache["prompt"] is not None and now - _cache["ts"] < _TTL:
        return _cache["prompt"], _cache["avoid"]
    prompt_rules, avoid_rules = [], []
    try:
        from sqlalchemy import text as _t
        from app.clients.mysql_client_manager import meta_mysql_client_manager
        async with meta_mysql_client_manager.session_factory() as s:
            r = await s.execute(_t(
                "SELECT rule_type, trigger_pattern, action FROM distilled_rules "
                "WHERE status='approved' ORDER BY created_at DESC LIMIT 200"))
            for x in r.fetchall():
                entry = {"type": x.rule_type, "trigger": x.trigger_pattern or "",
                         "action": x.action or ""}
                if x.rule_type in ("tool_choice", "alias"):
                    prompt_rules.append(entry)
                elif x.rule_type == "avoidance":
                    avoid_rules.append(entry)
    except Exception as e:
        logger.warning(f"[知识反馈] approved 规则加载失败(降级为空): {e}")
    _cache["prompt"] = prompt_rules
    _cache["avoid"] = avoid_rules
    _cache["ts"] = now
    return prompt_rules, avoid_rules


def build_prompt_block(prompt_rules: list[dict]) -> str:
    """构造注入 Agent 系统提示的文本块(tool_choice + alias)。

    action 可能是 JSON(如 {"tool":"xxx","hint":"..."})也可能是纯文本,
    两种都正确处理。
    """
    if not prompt_rules:
        return ""
    tc = [r for r in prompt_rules if r["type"] == "tool_choice"]
    alias = [r for r in prompt_rules if r["type"] == "alias"]
    lines = []
    if tc:
        tc_lines = []
        for r in tc[:20]:
            p = _safe_json(r["action"])
            tool = (p or {}).get("tool") or r["action"]
            tc_lines.append(f"用户说「{r['trigger']}」时通常需要 {tool}")
        lines.append("【历史经验:常见问题→工具组合】\n" + "\n".join(tc_lines))
    if alias:
        alias_lines = []
        for r in alias[:20]:
            p = _safe_json(r["action"])
            indicator = (p or {}).get("indicator") or r["action"]
            alias_lines.append(f"「{r['trigger']}」= 行内指标「{indicator}」")
        lines.append("【历史经验:用户术语映射】\n" + "\n".join(alias_lines))
    if not lines:
        return ""
    return "\n\n" + "\n\n".join(lines) + "\n(以上为系统积累的历史经验,供参考)"


def _safe_json(s: str) -> dict | None:
    """安全解析 JSON 字符串,失败返回 None。"""
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def build_avoidance_block(avoid_rules: list[dict]) -> str:
    """构造注入 generate_sql 的失败规避提示块。

    每条规则渲染:问句 + ✗曾生成的错误SQL + 报错 + ✓正确应为。"""
    if not avoid_rules:
        return ""
    lines = ["【历史教训:以下 SQL 模式曾出错,生成同类查询时规避】"]
    for r in avoid_rules[:30]:
        p = None
        try:
            p = json.loads(r["action"])
        except Exception:
            pass
        if p:
            q = (p.get("question") or r["trigger"] or "")[:80]
            bad = (p.get("bad_sql") or "")[:200]
            err = (p.get("error") or p.get("err") or "")[:120]
            good = (p.get("good_sql") or "")[:200]
            seg = f"- 问句「{q}」"
            if bad:
                seg += f"\n  ✗ 曾生成:{bad}"
            if err:
                seg += f"\n  报错:{err}"
            if good:
                seg += f"\n  ✓ 正确应为:{good}"
            lines.append(seg)
        else:
            lines.append(f"- {r['trigger']}: {r['action'][:150]}")
    return "\n".join(lines)


async def get_prompt_block() -> str:
    """Agent 系统提示注入块(tool_choice + alias)。"""
    prompt, _ = await _load_all()
    return build_prompt_block(prompt)


async def get_avoidance_block() -> str:
    """generate_sql 失败规避注入块(avoidance)。"""
    _, avoid = await _load_all()
    return build_avoidance_block(avoid)


# ======================== 语义规则:agent 分析 + 向量检索回灌 ========================
# 审核员标记 problem 的查询 → 凌晨3点 LLM 归纳隐藏规则 → 存 semantic_hint
# 查询时按问题 embedding 检索 top-3 相关规则 → 注入 Agent 系统提示

import numpy as np

_sem_cache = {"rules": None, "embeddings": None, "ts": 0.0}


async def _load_semantic_rules():
    """加载 approved semantic_hint 规则 + 预算 embedding(10分钟缓存)"""
    now = time.time()
    if _sem_cache["rules"] is not None and now - _sem_cache["ts"] < 600:
        return _sem_cache["rules"], _sem_cache["embeddings"]
    rules = []
    try:
        from sqlalchemy import text as _t
        from app.clients.mysql_client_manager import meta_mysql_client_manager
        async with meta_mysql_client_manager.session_factory() as s:
            r = await s.execute(_t(
                "SELECT id, trigger_pattern, action FROM distilled_rules "
                "WHERE status='approved' AND rule_type='semantic_hint' LIMIT 200"))
            for x in r.fetchall():
                p = None
                try:
                    p = json.loads(x.action or "")
                except Exception:
                    pass
                rules.append({"id": x.id, "trigger": x.trigger_pattern or "",
                              "rule": (p or {}).get("rule", "") if p else (x.action or "")})
    except Exception as e:
        logger.warning(f"[知识反馈] semantic_hint 加载失败: {e}")
    # 预算 embedding(复用 TEI 8081)
    embeddings = None
    if rules:
        texts = [r["rule"] or r["trigger"] for r in rules]
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("http://127.0.0.1:8081/embed", json={"inputs": texts})
                embeddings = np.array([item["embeddings"] for item in resp.json()])
        except Exception as e:
            logger.warning(f"[知识反馈] semantic_hint embedding 失败(降级空): {e}")
            embeddings = None
    _sem_cache["rules"] = rules
    _sem_cache["embeddings"] = embeddings
    _sem_cache["ts"] = now
    return rules, embeddings


async def get_semantic_hints(query: str, top_k: int = 3) -> str:
    """按当前查询 embedding 检索 top-k 相关 semantic_hint 规则 → 注入文本"""
    rules, embeddings = await _load_semantic_rules()
    if not rules or embeddings is None:
        return ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("http://127.0.0.1:8081/embed", json={"inputs": [query]})
            q_emb = np.array(resp.json()[0]["embeddings"])
        sims = np.dot(embeddings, q_emb) / (np.linalg.norm(embeddings, axis=1) * np.linalg.norm(q_emb) + 1e-9)
        top_idx = np.argsort(sims)[-top_k:][::-1]
        hits = [(rules[i], sims[i]) for i in top_idx if sims[i] > 0.45]
    except Exception as e:
        logger.warning(f"[知识反馈] semantic 检索失败(降级空): {e}")
        return ""
    if not hits:
        return ""
    lines = ["【语义规则(从用户反馈归纳)】"]
    for r, sim in hits:
        lines.append(f"- {r['rule']}")
    return "\n".join(lines)


async def analyze_problem_queries():
    """凌晨3点定时任务:把审核员标记 problem 的查询喂给 LLM 归纳隐藏规则。

    取 review_status='problem' 且 analyzed_at IS NULL 的条目,
    拼 prompt 让 LLM 抽共性规则(如"问最差/靠后→只取排名后N不返回全部"),
    存 distilled_rules(type=semantic_hint, status=approved, source=agent_analyzed),
    然后标记 analyzed_at 防重复。
    """
    from sqlalchemy import text as _t
    from app.clients.mysql_client_manager import meta_mysql_client_manager
    async with meta_mysql_client_manager.session_factory() as s:
        r = await s.execute(_t(
            "SELECT id, query_text, generated_sql, feedback, review_note "
            "FROM query_log WHERE review_status='problem' AND analyzed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 50"))
        rows = r.fetchall()
        if not rows:
            logger.info("[归纳] 无待分析的 problem 查询,跳过")
            return 0
        # 拼 prompt
        cases = []
        ids = []
        for x in rows:
            ids.append(x.id)
            cases.append(
                f"问题: {x.query_text}\n"
                f"生成SQL: {(x.generated_sql or '')[:200]}\n"
                f"用户反馈: {x.feedback or ''}\n"
                f"审核员描述: {x.review_note or ''}")
        prompt = (
            "你是银行金融问数系统的规则归纳器。以下是审核员标记为「有问题」的查询案例,"
            "每个含用户问题、系统生成的SQL、用户反馈、审核员的问题描述。\n"
            "请从这些案例中归纳出共性规则——系统在理解用户意图时常犯的语义错误,"
            "以及正确的行为应该是什么。每条规则一行,格式:\n"
            "规则: <当用户问...时,系统应该...>\n\n"
            + "\n---\n".join(cases) + "\n\n只输出规则,不要解释。"
        )
        # 调 LLM
        try:
            from app.agent.llm import llm
            from langchain_core.output_parsers import StrOutputParser
            from langchain_core.prompts import PromptTemplate
            chain = PromptTemplate.from_template("{p}") | llm | StrOutputParser()
            result = await chain.ainvoke({"p": prompt})
            logger.info(f"[归纳] LLM 输出:\n{result[:500]}")
        except Exception as e:
            logger.warning(f"[归纳] LLM 调用失败: {e}")
            return 0
        # 解析规则行 + 存 distilled_rules
        import re as _re
        new = 0
        for line in result.split("\n"):
            m = _re.match(r"规则[:：]\s*(.+)", line.strip())
            if not m:
                continue
            rule_text = m.group(1).strip()
            if len(rule_text) < 5:
                continue
            action = json.dumps({"rule": rule_text, "source_ids": ids[:10]},
                                ensure_ascii=False)
            await s.execute(_t(
                "INSERT INTO distilled_rules (rule_type, trigger_pattern, action, "
                "source, confidence, status, evidence_count) "
                "VALUES ('semantic_hint', :t, :a, 'agent_analyzed', 0.8, 'approved', :e)"),
                {"t": rule_text[:80], "a": action, "e": len(ids)})
            new += 1
        # 标记已分析
        for qid in ids:
            await s.execute(_t(
                "UPDATE query_log SET analyzed_at=NOW() WHERE id=:i"), {"i": qid})
        await s.commit()
        logger.info(f"[归纳] 从 {len(rows)} 条 problem 查询抽出 {new} 条 semantic_hint 规则")
        # 清 semantic 缓存让新规则下次检索生效
        _sem_cache["rules"] = None
        return new
