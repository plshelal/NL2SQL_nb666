"""外部数据执行器:缓存优先 + 结果归一化 + 超时降级。

返回统一结构:
{
  "rows":     [{...}, ...]      # 前端可直接渲染的行
  "markdown": "|日期|CPI...|"   # EDB 原生 markdown 表(报告用)
  "source":   "国家统计局(EDB)" # 溯源
  "kind":     "edb" | "news"
  "cached":   bool
}
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.agent.tools.ifind_mcp import ifind_mcp_manager
from app.core.log import logger


def _hash_key(server: str, payload: dict) -> str:
    return hashlib.md5(f"{server}|{json.dumps(payload, ensure_ascii=False, sort_keys=True)}".encode()).hexdigest()


def _normalize_edb(raw_text: str) -> dict | None:
    """EDB 返回:text 是一层 JSON 包裹,内含 answer(markdown 表) + datas[0].data.data(行列) + attrs(单位/来源)。"""
    try:
        outer = json.loads(raw_text)
        if outer.get("code") != 1:
            logger.warning(f"[external] EDB 业务失败: {outer.get('subMsg') or outer.get('msg')}")
            return None
        data = outer.get("data") or {}
        rows_src = None
        markdown = data.get("answer") or ""
        source = "iFinD EDB"
        # 结构化行列(datas[].data.data + columns + attrs)
        for d in (data.get("datas") or []):
            dd = ((d or {}).get("data") or {})
            table = dd.get("data")
            cols = dd.get("columns")
            attrs = dd.get("attrs") or {}
            if isinstance(table, list) and table and cols:
                rows_src = [dict(zip(cols, r)) for r in table]
                # 溯源信息:attrs 里任一指标的单位/来源
                first_attr = next(iter(attrs.values()), {}) if attrs else {}
                unit = first_attr.get("unit", "")
                src = first_attr.get("data_source", "iFinD")
                source = f"{src}{f'({unit})' if unit else ''}"
                break
        if rows_src is None and not markdown:
            return None
        # rows 转 str 以适配前端表格渲染(serialize 与内部 SQL 结果一致)
        rows = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in (rows_src or [])]
        if not rows and markdown:
            rows = [{"结果": markdown[:800]}]
        return {"rows": rows, "markdown": markdown, "source": source, "kind": "edb"}
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning(f"[external] EDB 归一化失败: {e}; 原文前200: {raw_text[:200]}")
        return None


def _normalize_news(raw_text: str) -> dict | None:
    """news 返回:data.data 是 JSON 字符串数组 [{资讯标题, 资讯内容, 日期, URL}]。"""
    try:
        outer = json.loads(raw_text)
        if outer.get("code") != 1:
            logger.warning(f"[external] news 业务失败: {outer.get('subMsg') or outer.get('msg')}")
            return None
        inner = (outer.get("data") or {}).get("data")
        items = json.loads(inner) if isinstance(inner, str) else (inner or [])
        if not isinstance(items, list):
            return None
        rows = []
        digest_lines = []
        for it in items[:5]:
            title = it.get("资讯标题") or it.get("标题") or ""
            content = (it.get("资讯内容") or it.get("内容") or "")[:300]
            date = it.get("日期") or ""
            url = it.get("URL") or ""
            rows.append({"日期": date, "标题": title, "摘要": content, "来源": url})
            digest_lines.append(f"- [{date}] {title}: {content[:120]}")
        if not rows:
            return None
        return {"rows": rows, "markdown": "\n".join(digest_lines), "source": "同花顺财经资讯", "kind": "news"}
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[external] news 归一化失败: {e}; 原文前200: {raw_text[:200]}")
        return None


class ExternalToolExecutor:
    """缓存(meta.external_data_cache)→ 调 MCP → 归一化 → 回写缓存。

    并发安全:与行内流水线共用同一 AsyncSession 会在并行工具调用时触发
    asyncmy 'readexactly() called while another coroutine is already waiting'
    (2026-08-18 实测)——故缓存读写使用独立短连接,不碰共享 session。
    """

    def __init__(self, meta_session=None):
        # meta_session 参数保留兼容但不再使用;独立建连
        self._factory = None

    def _get_factory(self):
        if self._factory is None:
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            self._factory = meta_mysql_client_manager.session_factory
        return self._factory

    def _cache_session(self):
        """独立短连接上下文(用后即关,连接池复用)。"""
        return self._get_factory()()

    async def _cache_get(self, key: str) -> dict | None:
        try:
            async with self._cache_session() as s:
                r = await s.execute(
                    text("SELECT result_json FROM external_data_cache WHERE query_hash=:k AND expires_at > NOW()"),
                    {"k": key},
                )
                row = r.fetchone()
                return json.loads(row.result_json) if row else None
        except Exception as e:
            logger.warning(f"[external] 缓存读失败(忽略): {e}")
            return None

    async def _cache_put(self, key: str, tool_name: str, payload: dict, result: dict, ttl_hours: float):
        try:
            async with self._cache_session() as s:
                await s.execute(text("""
                    INSERT INTO external_data_cache (query_hash, tool_name, params, result_json, source, fetched_at, expires_at)
                    VALUES (:k,:t,:p,:r,:s,NOW(),NOW() + INTERVAL :h HOUR)
                    ON DUPLICATE KEY UPDATE result_json=VALUES(result_json), fetched_at=NOW(), expires_at=NOW() + INTERVAL :h HOUR
                """), {"k": key, "t": tool_name, "p": json.dumps(payload, ensure_ascii=False),
                       "r": json.dumps(result, ensure_ascii=False), "s": result.get("source", ""), "h": int(ttl_hours)})
                await s.commit()
        except Exception as e:
            logger.warning(f"[external] 缓存写失败(忽略): {e}")

    async def query_edb(self, query: str) -> dict | None:
        payload = {"server": "edb", "query": query}
        key = _hash_key("edb", payload)
        cached = await self._cache_get(key)
        if cached:
            cached["cached"] = True
            logger.info(f"[external] EDB 缓存命中: {query[:60]}")
            return cached
        logger.info(f"[external] 调用 iFinD EDB: {query[:80]}")
        res = await ifind_mcp_manager.call_edb(query)
        if not res.get("ok"):
            return None
        normalized = _normalize_edb(res["text"])
        if normalized:
            normalized["cached"] = False
            ttl = 24
            await self._cache_put(key, "get_edb_data", payload, normalized, ttl)
        return normalized

    async def query_news(self, query: str, time_start: str = None, time_end: str = None) -> dict | None:
        payload = {"server": "news", "query": query, "time_start": time_start, "time_end": time_end}
        key = _hash_key("news", payload)
        cached = await self._cache_get(key)
        if cached:
            cached["cached"] = True
            logger.info(f"[external] news 缓存命中: {query[:60]}")
            return cached
        logger.info(f"[external] 调用 iFinD news: {query[:80]}")
        res = await ifind_mcp_manager.call_news(query, time_start, time_end, size=3)
        if not res.get("ok"):
            return None
        normalized = _normalize_news(res["text"])
        if normalized:
            normalized["cached"] = False
            await self._cache_put(key, "search_news", payload, normalized, 2)
        return normalized


def format_external_ctx(external: dict | list) -> str:
    """把外部结果编排成报告 prompt 的上下文块(归属清晰,防因果脑补)。"""
    items = external if isinstance(external, list) else [external]
    blocks = []
    for ex in items:
        if not ex:
            continue
        head = f"[外部数据 · {ex.get('source','未知来源')} · {ex.get('kind','')}]"
        body = ex.get("markdown") or json.dumps(ex.get("rows", [])[:10], ensure_ascii=False)
        blocks.append(f"{head}\n{body[:1500]}")
    if not blocks:
        return ""
    return ("外部参考数据(仅供参考关联,非库内数据):\n" + "\n\n".join(blocks) +
            "\n\n分析要求:先陈述内部数据事实,再陈述外部数据事实(注明来源),"
            "只允许表述'时间上吻合/走势一致',严禁下因果结论。")
