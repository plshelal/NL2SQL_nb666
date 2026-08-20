"""iFinD MCP 客户端管理器。

实测结论(2026-08 探测):
- 端点为 streamable HTTP 形态(SSE GET 握手返回 405),用 mcp SDK 的 streamable_http_client
- 本项目锁定的 mcp SDK 版本:streamable_http_client(url, http_client=...) 只收 2 元组,headers 通过 httpx.AsyncClient 注入
- CallToolResult 属性为 is_error(非 isError)
- 工具清单(启动时 list_tools 校验):
  edb : get_edb_data  {query}                          宏观/行业指标
  news: search_news   {query, time_start, time_end, size} 财经资讯
        search_notice {query, time_start, time_end, size} 公告
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.core.log import logger

_CONF_PATH = Path(__file__).parent.parent.parent.parent / "conf" / "tools.yaml"

with open(_CONF_PATH, "r", encoding="utf-8") as f:
    _conf = yaml.safe_load(f) or {}

IFIND_CONF: dict = _conf.get("ifind", {})
EXTERNAL_TERMS: dict = _conf.get("external_terms", {})
INTERNAL_TERMS: list = _conf.get("internal_terms", [])
AMBIGUOUS_ROOTS: dict = _conf.get("ambiguous_roots", {})
VAGUE_QUANTIFIERS: list = _conf.get("vague_quantifiers", [])
MACRO_ALIASES: dict = _conf.get("macro_aliases", {})


def _external_enabled() -> bool:
    """外部数据模块总开关:yaml external_enabled,环境变量 EXTERNAL_ENABLED=0/1 覆盖。"""
    import os
    env = os.getenv("EXTERNAL_ENABLED")
    if env is not None:
        return env.strip() not in ("0", "false", "False", "")
    return bool(_conf.get("external_enabled", True))


EXTERNAL_ENABLED: bool = _external_enabled()


def _token() -> str:
    return os.getenv("IFIND_MCP_TOKEN", "")


class IfindMcpClient:
    """单个 MCP server 的连接管理:懒连接 + 断线重连 + 单飞锁防并发重连。"""

    def __init__(self, name: str, url: str, timeout_ms: int = 20000):
        self.name = name
        self.url = url
        self.timeout = timeout_ms / 1000
        self._session = None
        self._cm_stack = None
        self._http = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._session is not None:
            return self._session
        async with self._lock:
            if self._session is not None:
                return self._session
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            self._http = httpx.AsyncClient(
                headers={"Authorization": _token()}, timeout=self.timeout
            )
            # 注意:本项目 mcp SDK 版本的 streamable_http_client 收 (url, http_client),
            # 产出 2 元组 (read, write);headers 经 http_client 默认头注入
            self._cm_stack = streamable_http_client(url=self.url, http_client=self._http)
            streams = await self._cm_stack.__aenter__()
            read, write = streams[0], streams[1]
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            self._session = session
            logger.info(f"[iFinD MCP] {self.name} 已连接")
            return self._session

    async def _reset(self):
        """断线后清理,下次调用重连。"""
        try:
            if self._session is not None:
                await self._session.__aexit__(None, None, None)
            if self._cm_stack is not None:
                await self._cm_stack.__aexit__(None, None, None)
        except Exception:
            pass
        finally:
            self._session = None
            self._cm_stack = None

    async def list_tools(self) -> list[dict]:
        s = await self._ensure()
        tl = await s.list_tools()
        return [{"name": t.name, "description": (t.description or "")[:200]} for t in tl.tools]

    async def call(self, tool: str, args: dict) -> dict[str, Any]:
        """调用工具,返回 {ok, text}。失败自动重连一次。"""
        for attempt in (1, 2):
            try:
                s = await self._ensure()
                res = await asyncio.wait_for(s.call_tool(tool, args), timeout=self.timeout)
                is_err = getattr(res, "is_error", getattr(res, "isError", False))
                texts = [getattr(c, "text", str(c)) for c in (res.content or [])]
                return {"ok": not is_err, "text": "\n".join(texts)}
            except BaseException as e:
                subs = getattr(e, "exceptions", None)
                real = "; ".join(f"{type(x).__name__}:{str(x)[:120]}" for x in subs) if subs else f"{type(e).__name__}:{str(e)[:120]}"
                logger.warning(f"[iFinD MCP] {self.name}.{tool} 第{attempt}次失败: {real}")
                await self._reset()
                if attempt == 2:
                    return {"ok": False, "error": real}
        return {"ok": False, "error": "unreachable"}

    async def close(self):
        await self._reset()
        if self._http is not None:
            await self._http.aclose()
            self._http = None


class IfindMcpManager:
    """edb + news 两个 server 的统一入口。"""

    def __init__(self):
        self._clients: dict[str, IfindMcpClient] = {}

    def _get(self, name: str) -> IfindMcpClient:
        if name not in self._clients:
            c = IFIND_CONF.get(name)
            if not c:
                raise ValueError(f"未知 iFinD server: {name}")
            self._clients[name] = IfindMcpClient(name, c["url"], c.get("timeout_ms", 20000))
        return self._clients[name]

    async def call_edb(self, query: str) -> dict:
        tool = IFIND_CONF.get("edb", {}).get("tool", "get_edb_data")
        return await self._get("edb").call(tool, {"query": query})

    async def call_news(self, query: str, time_start: str = None, time_end: str = None, size: int = 3) -> dict:
        """search_news 实测 time_start/time_end 为必填(MCP schema 强制),
        未显式给出时默认近一年,避免 Invalid arguments。"""
        from datetime import date, timedelta
        tool = IFIND_CONF.get("news", {}).get("tool", "search_news")
        end = time_end or date.today().isoformat()
        start = time_start or (date.today() - timedelta(days=365)).isoformat()
        args = {"query": query, "time_start": start, "time_end": end, "size": size}
        return await self._get("news").call(tool, args)

    async def close(self):
        for c in self._clients.values():
            await c.close()
        self._clients.clear()


ifind_mcp_manager = IfindMcpManager()
