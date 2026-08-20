"""知识管理 API · 仅管理员可访问

三栏对应:
- 知识总览: 计算公式(indicator_formula 表) + 指标组(knowledge/indicator_groups/*.md 文件) 只读展示
- 审核中心: RubikSQL 蒸馏规则(distilled_rules) 的人工审核闭环 pending→approved/rejected
- 添加知识: 新建计算公式(→ indicator_formula) / 新建指标组(→ .md 文件),lookup 工具自动生效

反馈通路(现成,不改): distilled_rules.status=approved 且 type∈{tool_choice,alias} 的规则,
由 orchestrator._augment_system_prompt 注入 Agent 系统提示(10 分钟缓存)。

挖矿(distill): 从 experience_log 的 success 行挖「问句→SQL」fewshot 候选 → 写 pending,
供审核员裁定。经验日志为空则返回 0。
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session, get_dw_session
from app.api.routers.auth_router import get_current_user
from app.config import ROOT_DIR
from app.core.log import logger

review_router = APIRouter()

ADMIN = "admin_total"


def _require_admin(user) -> None:
    if user.username != ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可访问")


# ======================== 审核中心 ========================

@review_router.get("/api/knowledge/query-review")
async def list_query_review(status: str = "pending", user=Depends(get_current_user),
                            session: AsyncSession = Depends(get_meta_session)):
    """列出带用户反馈的查询(审核员复核语义对错)。
    status: pending(待复核) / correct / problem。
    卡片展示 问题+SQL+结果摘要+用户反馈(预填问题描述栏)。"""
    _require_admin(user)
    if status not in ("pending", "correct", "problem"):
        raise HTTPException(400, "status 非法")
    r = await session.execute(text(
        "SELECT id, username, query_text, generated_sql, result_summary, "
        "feedback, review_status, review_note, tool_trace, created_at FROM query_log "
        "WHERE review_status=:s ORDER BY created_at DESC LIMIT 100"
    ), {"s": status})
    out = []
    for x in r.fetchall():
        out.append({
            "id": x.id, "username": x.username, "question": x.query_text or "",
            "sql": x.generated_sql or "",
            "result": (x.result_summary or "")[:600],
            "feedback": x.feedback or "",
            "review_status": x.review_status, "review_note": x.review_note or "",
            "tool_trace": x.tool_trace or "",
            "time": str(x.created_at) if x.created_at else "",
        })
    return out


@review_router.post("/api/knowledge/query-review/{qid}")
async def review_query(qid: int, body=Body(...), user=Depends(get_current_user),
                       session: AsyncSession = Depends(get_meta_session)):
    """审核员复核一条查询: {status: correct|problem, note?: 补充描述}
    correct → 该查询的 问句→SQL 回灌 fewshot 检索池;problem → 进 agent 分析池。"""
    _require_admin(user)
    new_status = body.get("status")
    if new_status not in ("correct", "problem"):
        raise HTTPException(400, "status 非法")
    note = body.get("note")
    # 取该查询的问题+SQL(回灌用)
    r = await session.execute(text(
        "SELECT query_text, generated_sql, feedback FROM query_log WHERE id=:i"), {"i": qid})
    row = r.fetchone()
    if not row:
        raise HTTPException(404, "查询不存在")
    await session.execute(text(
        "UPDATE query_log SET review_status=:s, review_note=:n WHERE id=:i"),
        {"s": new_status, "n": (note or "")[:2000], "i": qid})
    await session.commit()
    logger.info(f"[审核] 查询#{qid} → {new_status}")

    # correct → 回灌 fewshot(问句→SQL)
    feedback = None
    if new_status == "correct" and row.generated_sql:
        try:
            from app.agent.fewshot_rag import add_example
            q = (row.query_text or "").strip()
            s = (row.generated_sql or "").strip()
            if q and s and re.search(r"SELECT|WITH", s, re.IGNORECASE):
                feedback = await add_example(q, s)
                logger.info(f"[审核] 查询#{qid} SQL 回灌 fewshot: {feedback}")
        except Exception as e:
            logger.warning(f"[审核] fewshot 回灌失败(不阻断): {e}")
            feedback = {"ok": False, "reason": str(e)}
    return {"ok": True, "feedback": feedback}


# ---- 旧:蒸馏规则审核(保留兼容,新流程用 query-review) ----

@review_router.get("/api/knowledge/review")
async def list_review(status: str = "pending", user=Depends(get_current_user),
                      session: AsyncSession = Depends(get_meta_session)):
    """列出蒸馏规则。status: pending / approved / rejected"""
    _require_admin(user)
    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(400, "status 非法")
    r = await session.execute(text(
        "SELECT id, rule_type, trigger_pattern, action, source, confidence, "
        "status, evidence_count, created_at FROM distilled_rules "
        "WHERE status=:s ORDER BY created_at DESC LIMIT 300"
    ), {"s": status})
    out = []
    for x in r.fetchall():
        action_raw = x.action or ""
        parsed = None
        try:
            parsed = json.loads(action_raw)
        except Exception:
            pass
        out.append({
            "id": x.id, "type": x.rule_type, "trigger": x.trigger_pattern,
            "action": action_raw, "parsed": parsed, "source": x.source or "",
            "confidence": float(x.confidence or 0), "status": x.status,
            "evidence_count": int(x.evidence_count or 0),
            "time": str(x.created_at) if x.created_at else "",
        })
    return out


@review_router.post("/api/knowledge/review/batch")
async def review_batch(body=Body(...), user=Depends(get_current_user),
                       session: AsyncSession = Depends(get_meta_session)):
    """批量审核: {ids: [...], status: approved|rejected}

    注意:本路由必须注册在 /review/{rid} 之前,否则 'batch' 会被 {rid} 吸走导致 422。
    """
    _require_admin(user)
    ids = body.get("ids", [])
    st = body.get("status")
    if st not in ("approved", "rejected") or not ids:
        raise HTTPException(400, "参数非法")
    for i in ids:
        await session.execute(text(
            "UPDATE distilled_rules SET status=:s WHERE id=:i"), {"s": st, "i": i})
    await session.commit()
    logger.info(f"[审核] 批量 {len(ids)} 条 → {st}")
    return {"ok": True, "count": len(ids)}


@review_router.post("/api/knowledge/review/{rid}")
async def review_one(rid: int, body=Body(...), user=Depends(get_current_user),
                     session: AsyncSession = Depends(get_meta_session)):
    """审核单条: {status: approved|rejected, action?: 编辑后的 action(可选)}

    fewshot 规则通过时自动回灌到 fewshot_rag 检索池(add_example,幂等);
    失败降级不阻断审核(文件落盘已成功则下次启动补算向量)。
    """
    _require_admin(user)
    new_status = body.get("status")
    if new_status not in ("approved", "rejected"):
        raise HTTPException(400, "status 非法")
    edited = body.get("action")
    # 取规则类型 + 当前 action(回灌判断用)
    rrow = (await session.execute(text(
        "SELECT rule_type, action FROM distilled_rules WHERE id=:i"), {"i": rid})).fetchone()
    if edited is not None:
        await session.execute(text(
            "UPDATE distilled_rules SET status=:s, action=:a WHERE id=:i"),
            {"s": new_status, "a": str(edited), "i": rid})
    else:
        await session.execute(text(
            "UPDATE distilled_rules SET status=:s WHERE id=:i"),
            {"s": new_status, "i": rid})
    await session.commit()
    logger.info(f"[审核] 规则#{rid} → {new_status}(action edited={edited is not None})")

    # 回灌:fewshot 通过 → 追加进 fewshot_rag 检索池
    feedback = None
    if new_status == "approved" and rrow and rrow.rule_type == "fewshot":
        try:
            from app.agent.fewshot_rag import add_example
            action_raw = edited if edited is not None else (rrow.action or "")
            q, s = "", ""
            try:
                p = json.loads(action_raw)
                q, s = p.get("question", ""), p.get("sql", "")
            except Exception:
                pass
            if q and s:
                feedback = await add_example(q, s)
                logger.info(f"[审核] fewshot#{rid} 回灌检索池: {feedback}")
        except Exception as e:
            logger.warning(f"[审核] fewshot 回灌失败(不阻断审核): {e}")
            feedback = {"ok": False, "reason": str(e)}
    return {"ok": True, "feedback": feedback}


@review_router.post("/api/knowledge/distill")
async def distill(user=Depends(get_current_user),
                  session: AsyncSession = Depends(get_meta_session)):
    """在线挖矿:从 experience_log 蒸馏两类候选 → 写 pending。

    - fewshot:success 行的 (query_text, final_sql) 直接成问答范例候选
    - avoidance:correction_event 行的 (问句, 原错SQL, 报错, 纠对SQL) 成失败规避候选
      (correct_sql 写经验日志时 error_message="orig=<bad> | err=<err>",final_sql=纠对SQL)
    去重:trigger_pattern(问句前 80 字)按类型查重。
    """
    _require_admin(user)
    # 已有待审规则的 trigger,按类型分桶去重
    exist = await session.execute(text(
        "SELECT rule_type, trigger_pattern FROM distilled_rules "
        "WHERE status='pending' AND rule_type IN ('fewshot','avoidance')"))
    seen_fs = set()
    seen_av = set()
    for x in exist.fetchall():
        (seen_fs if x.rule_type == "fewshot" else seen_av).add(x.trigger_pattern)

    new_fs = 0
    # ---- fewshot:success 行 ----
    r = await session.execute(text(
        "SELECT query_text, final_sql FROM experience_log "
        "WHERE outcome='success' AND final_sql IS NOT NULL AND final_sql!='' "
        "ORDER BY id DESC LIMIT 500"
    ))
    succ_rows = r.fetchall()
    for row in succ_rows:
        q = (row.query_text or "").strip()
        s = (row.final_sql or "").strip()
        if not q or not s or not re.search(r"SELECT|WITH", s, re.IGNORECASE):
            continue
        trig = q[:80]
        if trig in seen_fs:
            continue
        seen_fs.add(trig)
        await session.execute(text(
            "INSERT INTO distilled_rules (rule_type, trigger_pattern, action, "
            "source, confidence, status, evidence_count) "
            "VALUES ('fewshot', :t, :a, 'auto_distilled', 0.6, 'pending', 1)"),
            {"t": trig, "a": json.dumps({"question": q, "sql": s}, ensure_ascii=False)})
        new_fs += 1

    # ---- avoidance:correction_event 行(含原错SQL/报错/纠对SQL 三元组)----
    new_av = 0
    r2 = await session.execute(text(
        "SELECT query_text, final_sql, error_message FROM experience_log "
        "WHERE outcome='correction_event' AND final_sql IS NOT NULL "
        "AND error_message LIKE 'orig=%' ORDER BY id DESC LIMIT 300"
    ))
    for row in r2.fetchall():
        em = row.error_message or ""
        m = re.match(r"orig=(.*?)\s*\|\s*err=(.*)", em, re.S)
        if not m:
            continue
        bad, err = m.group(1).strip(), m.group(2).strip()
        q = (row.query_text or "").strip()
        good = (row.final_sql or "").strip()
        if not bad or not good:
            continue
        trig = q[:80]
        if trig in seen_av:
            continue
        seen_av.add(trig)
        await session.execute(text(
            "INSERT INTO distilled_rules (rule_type, trigger_pattern, action, "
            "source, confidence, status, evidence_count) "
            "VALUES ('avoidance', :t, :a, 'auto_distilled', 0.7, 'pending', 1)"),
            {"t": trig, "a": json.dumps({"question": q, "bad_sql": bad,
                                         "good_sql": good, "error": err},
                                        ensure_ascii=False)})
        new_av += 1

    await session.commit()
    new = new_fs + new_av
    logger.info(f"[挖矿] fewshot {new_fs} + avoidance {new_av} = {new} 条候选")
    return {"new": new, "fewshot": new_fs, "avoidance": new_av,
            "msg": f"挖出 fewshot {new_fs} + avoidance {new_av} 条候选"}


@review_router.post("/api/knowledge/demo-seed")
async def demo_seed(user=Depends(get_current_user),
                    session: AsyncSession = Depends(get_meta_session)):
    """填充演示条目(仅当 pending 为空时,source 标记 demo 便于清理)。

    让审核界面在经验日志尚未积累时也能演示完整 approve/reject 流程。
    """
    _require_admin(user)
    cur = await session.execute(text(
        "SELECT COUNT(*) AS n FROM distilled_rules WHERE status='pending'"))
    if cur.scalar() > 0:
        return {"ok": False, "msg": "已有待审条目,无需填充演示数据"}
    samples = [
        ("fewshot", "A市农商行2026年3月31日不良贷款率是多少",
         json.dumps({"question": "A市农商行2026年3月31日不良贷款率是多少",
                     "sql": "SELECT o.org_name, d.index_value AS 不良贷款率, il.index_unit AS 单位 "
                            "FROM index_data d JOIN org_info o ON d.org_code=o.org_code "
                            "JOIN index_list il ON d.index_name=il.index_name "
                            "WHERE o.org_name='江苏省A市农商行' AND d.index_name='不良贷款率' "
                            "AND d.data_date='2026-03-31'"}, ensure_ascii=False)),
        ("alias", "人均创利",
         json.dumps({"alias": "人均创利", "indicator": "人均利润",
                     "samples": ["E市农商行人均创利多少", "人均净利润排名"]},
                    ensure_ascii=False)),
        ("tool_choice", "CPI/利率/PPI",
         json.dumps({"hint": "问句含「CPI/利率/PPI」时推荐调用 query_macro_indicator",
                     "tool": "query_macro_indicator", "success_count": 5},
                    ensure_ascii=False)),
    ]
    for rtype, trig, action in samples:
        await session.execute(text(
            "INSERT INTO distilled_rules (rule_type, trigger_pattern, action, "
            "source, confidence, status, evidence_count) "
            "VALUES (:rt, :t, :a, 'demo', :c, 'pending', :e)"),
            {"rt": rtype, "t": trig, "a": action,
             "c": 0.8 if rtype != "fewshot" else 0.6, "e": 3})
    await session.commit()
    logger.info("[审核] 填充 3 条演示条目(source=demo)")
    return {"ok": True, "msg": "已填充 3 条演示条目"}


# ======================== 知识总览 ========================

@review_router.get("/api/knowledge/formulas")
async def list_formulas(user=Depends(get_current_user),
                        session: AsyncSession = Depends(get_meta_session)):
    """计算公式清单(来自 indicator_formula 表,lookup_formula 工具的数据源)。
    所有登录用户可读(知识展示);写入仍需管理员。"""
    r = await session.execute(text(
        "SELECT term, aliases, formula_type, index_names, sql_template, description "
        "FROM indicator_formula ORDER BY term"))
    out = []
    for x in r.fetchall():
        def _loads(v):
            if not v:
                return []
            if isinstance(v, (list, dict)):
                return v
            try:
                return json.loads(v)
            except Exception:
                return []
        out.append({
            "term": x.term, "aliases": _loads(x.aliases),
            "formula_type": x.formula_type, "index_names": _loads(x.index_names),
            "sql_template": x.sql_template, "description": x.description or "",
        })
    return out


def _groups_data() -> list[dict]:
    """解析 knowledge/indicator_groups/*.md(lookup_indicator_group 数据源)。

    抽成内部函数供 stats 复用,避免 stats 走 HTTP 自调。
    """
    kdir = ROOT_DIR / "knowledge" / "indicator_groups"
    out = []
    if not kdir.exists():
        return out
    for f in sorted(kdir.glob("*.md")):
        text_all = f.read_text(encoding="utf-8")
        lines = text_all.split("\n")
        name = lines[0].lstrip("# ").strip() if lines else f.stem
        desc = ""
        aliases = ""
        indicators = []
        in_inds = False
        for ln in lines[1:]:
            ls = ln.strip()
            if ls.startswith("**描述**"):
                desc = ls.replace("**描述**:", "").replace("**描述**：", "").strip()
            elif ls.startswith("**别名**"):
                aliases = ls.replace("**别名**:", "").replace("**别名**：", "").strip()
            elif ls.startswith("## "):
                in_inds = "指标" in ls
            elif in_inds and ls.startswith("- "):
                indicators.append(ls[2:].strip())
        out.append({"file": f.name, "name": name, "description": desc,
                    "aliases": aliases, "indicators": indicators})
    return out


@review_router.get("/api/knowledge/groups")
async def list_groups(user=Depends(get_current_user)):
    """指标组清单(来自 knowledge/indicator_groups/*.md 文件,
    lookup_indicator_group 工具的数据源)。解析 .md 结构。
    所有登录用户可读(知识展示);写入仍需管理员。"""
    return _groups_data()


@review_router.get("/api/knowledge/stats")
async def stats(user=Depends(get_current_user),
               session: AsyncSession = Depends(get_meta_session),
               dw_session: AsyncSession = Depends(get_dw_session)):
    """知识图谱数据:经验时间线 + 规则状态/类型计数 + 指标覆盖矩阵。

    服务于:① 经验演化时间线 ② 规则状态分布 ③ 覆盖热力矩阵(发现知识薄弱指标)。
    """
    _require_admin(user)
    import collections as _col

    def _loads(v):
        if not v:
            return []
        if isinstance(v, (list, dict)):
            return v
        try:
            return json.loads(v)
        except Exception:
            return []

    # 1. 经验时间线:experience_log 按天按 outcome
    r = await session.execute(text(
        "SELECT DATE(created_at) d, outcome, COUNT(*) n FROM experience_log "
        "GROUP BY DATE(created_at), outcome ORDER BY d"))
    by_date: dict = _col.defaultdict(
        lambda: {"total": 0, "success": 0, "corrected": 0, "failed": 0,
                 "correction_event": 0, "agent_trace": 0})
    for x in r.fetchall():
        d = str(x.d) if x.d else "未知"
        e = by_date[d]
        e["total"] += x.n
        if x.outcome in e:
            e[x.outcome] += x.n
    timeline = [{"date": d, **v} for d, v in sorted(by_date.items())]

    # 2. 规则状态 + 类型计数
    r = await session.execute(text(
        "SELECT status, rule_type, COUNT(*) n FROM distilled_rules "
        "GROUP BY status, rule_type"))
    status_cnt, type_cnt = _col.Counter(), _col.Counter()
    for x in r.fetchall():
        status_cnt[x.status] += x.n
        type_cnt[x.rule_type] += x.n

    # 3. 公式(组件 + 别名)
    fr = await session.execute(text(
        "SELECT term, aliases, index_names FROM indicator_formula"))
    formulas = [{"term": x.term, "aliases": _loads(x.aliases),
                 "index_names": _loads(x.index_names)} for x in fr.fetchall()]

    # 4. 指标组
    groups = _groups_data()

    # 5. 直接指标 + 被查询次数(从 final_sql 提取 index_name='X')
    ir = await dw_session.execute(text(
        "SELECT DISTINCT index_name FROM index_list WHERE index_name IS NOT NULL"))
    indicators = [x.index_name for x in ir.fetchall() if x.index_name]
    qcnt = _col.Counter()
    sql_rows = await session.execute(text(
        "SELECT final_sql FROM experience_log WHERE final_sql IS NOT NULL AND final_sql!=''"))
    for x in sql_rows.fetchall():
        for m in re.findall(r"index_name\s*=\s*'([^']+)'",
                            x.final_sql or "", re.IGNORECASE):
            qcnt[m] += 1

    # 6. 覆盖矩阵:每个直接指标的各维度覆盖
    ind2group = {ind: g["name"] for g in groups for ind in g["indicators"]}
    comp_set = set()
    alias_cnt = _col.Counter()
    for f in formulas:
        for c in f["index_names"]:
            comp_set.add(c)
            alias_cnt[c] += len(f["aliases"])
    coverage = [{
        "indicator": ind,
        "in_formula": ind in comp_set,
        "group": ind2group.get(ind),
        "aliases": alias_cnt.get(ind, 0),
        "queried": qcnt.get(ind, 0),
    } for ind in indicators]

    return {
        "timeline": timeline,
        "rule_status": dict(status_cnt),
        "rule_types": dict(type_cnt),
        "coverage": coverage,
        "totals": {
            "indicators": len(indicators),
            "formulas": len(formulas),
            "groups": len(groups),
            "experience": sum(v["total"] for v in by_date.values()),
        },
    }


# ======================== 添加知识 ========================

@review_router.get("/api/knowledge/indicators")
async def list_indicators(user=Depends(get_current_user),
                           session: AsyncSession = Depends(get_dw_session)):
    """可选指标名清单(来自 index_list,供添加公式时点选组件指标)。
    所有登录用户可读(知识展示/图谱);添加公式仍需管理员。"""
    r = await session.execute(text(
        "SELECT DISTINCT index_name FROM index_list "
        "WHERE index_name IS NOT NULL ORDER BY index_name"))
    return [x.index_name for x in r.fetchall() if x.index_name]


@review_router.post("/api/knowledge/formula")
async def add_formula(body=Body(...), user=Depends(get_current_user),
                      session: AsyncSession = Depends(get_meta_session)):
    """新建计算公式 → 写 indicator_formula 表(lookup_formula 自动生效)。
    body: {term, aliases[], index_names[], sql_template, description}
    """
    _require_admin(user)
    term = (body.get("term") or "").strip()
    if not term:
        raise HTTPException(400, "指标名不可为空")
    aliases = body.get("aliases") or []
    index_names = body.get("index_names") or []
    sql_template = (body.get("sql_template") or "").strip()
    description = (body.get("description") or "").strip()
    if not sql_template:
        raise HTTPException(400, "SQL 模板不可为空")
    await session.execute(text(
        "INSERT INTO indicator_formula (term, aliases, formula_type, index_names, "
        "sql_template, description) VALUES (:t,:a,'computed',:n,:s,:d) "
        "ON DUPLICATE KEY UPDATE aliases=VALUES(aliases), index_names=VALUES(index_names), "
        "sql_template=VALUES(sql_template), description=VALUES(description)"),
        {"t": term, "a": json.dumps(aliases, ensure_ascii=False),
         "n": json.dumps(index_names, ensure_ascii=False),
         "s": sql_template, "d": description})
    await session.commit()
    logger.info(f"[添加知识] 新公式: {term} (组件 {index_names})")
    return {"ok": True, "term": term}


@review_router.post("/api/knowledge/group")
async def add_group(body=Body(...), user=Depends(get_current_user)):
    """新建指标组 → 写 knowledge/indicator_groups/<name>.md
    (lookup_indicator_group 工具自动 glob 到,无需重启)。
    body: {name, description, aliases[], indicators[]}
    """
    _require_admin(user)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "组名不可为空")
    description = (body.get("description") or "").strip()
    aliases = body.get("aliases") or []
    indicators = [i.strip() for i in (body.get("indicators") or []) if i.strip()]
    kdir = ROOT_DIR / "knowledge" / "indicator_groups"
    kdir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "", name)
    fpath = kdir / f"{safe}.md"
    lines = [f"# {name}", ""]
    lines.append(f"**描述**: {description or ''}")
    lines.append(f"**别名**: {'、'.join(aliases) if aliases else ''}")
    lines.append("")
    lines.append("## 包含指标")
    for ind in indicators:
        lines.append(f"- {ind}")
    lines.append("")
    fpath.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"[添加知识] 新指标组文件: {fpath.name} ({len(indicators)} 个指标)")
    return {"ok": True, "file": fpath.name}


# ======================== MCP 连接展示 ========================

@review_router.get("/api/tools/mcp")
async def list_mcp(user=Depends(get_current_user)):
    """聊天框 MCP icon 用:返回管理员已配置的外部 MCP 连接(只读展示)。
    读 conf/tools.yaml,目前即同花顺 iFinD(EDB + 资讯)。所有登录用户可看。"""
    import yaml as _yaml
    cfg_path = ROOT_DIR / "conf" / "tools.yaml"
    out = []
    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        ext_on = cfg.get("external_enabled", True)
        ifind = cfg.get("ifind") or {}
        tools = []
        labels = {"edb": "宏观经济指标", "news": "财经资讯检索"}
        descs = {"edb": "CPI/PPI/LPR/GDP/PMI/社融/汇率等行外经济数据",
                 "news": "地产政策/央行货政/行业动态/市场热点"}
        for key in ("edb", "news"):
            sub = ifind.get(key)
            if sub:
                tools.append({"key": key, "label": labels.get(key, key),
                              "tool": sub.get("tool", ""), "desc": descs.get(key, "")})
        if tools:
            out.append({"name": "同花顺 iFinD", "enabled": bool(ext_on), "tools": tools})
    except Exception as e:
        logger.warning(f"[MCP展示] 读取 tools.yaml 失败: {e}")
    return out
