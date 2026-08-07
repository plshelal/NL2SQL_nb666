from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_query_service, get_meta_session
from app.api.routers.auth_router import get_current_user
from app.api.schemas.query_schema import QuerySchema
from app.query_service import QueryService
import json
from datetime import datetime

query_router = APIRouter()


@query_router.post("/api/query", summary="金融问数 SSE 查询")
async def query_nl2sql(
    query: QuerySchema,
    service: QueryService = Depends(get_query_service),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_meta_session),
):
    # 查用户权限(直接指标 + 计算指标本身的授权)
    result = await session.execute(
        text("SELECT indicator_name FROM role_permission WHERE role_name = :r"),
        {"r": user.position}
    )
    allowed_indicators = [r.indicator_name for r in result.fetchall()]

    # 展开计算指标的组件指标:用户有权访问的计算指标(term),
    # 其 index_names 组件指标并入白名单。
    # 因为生成的 SQL 里写的是 index_name='各项贷款余额'(组件名),
    # 而非 index_name='存贷比'(计算指标名),不展开则白名单校验会误拦合法计算查询。
    component_rows = await session.execute(
        text("""
            SELECT f.index_names
            FROM indicator_formula f
            WHERE f.term IN (
                SELECT indicator_name FROM role_permission WHERE role_name = :r
            )
        """),
        {"r": user.position}
    )
    component_inds = set()
    for cr in component_rows.fetchall():
        try:
            component_inds.update(json.loads(cr.index_names))
        except (json.JSONDecodeError, TypeError):
            pass
    allowed_indicators = list(set(allowed_indicators) | component_inds)

    # 查机构范围
    result2 = await session.execute(
        text("SELECT allowed_orgs FROM users WHERE username = :u"),
        {"u": user.username}
    )
    row = result2.fetchone()
    allowed_orgs = json.loads(row.allowed_orgs) if row and row.allowed_orgs else []

    # 综合管理(行领导)为全权管理员,不靠指标数量推断
    is_admin = user.position == "综合管理"

    perms = {
        "allowed_indicators": allowed_indicators,
        "allowed_orgs": allowed_orgs,
        "position": user.position,
        "is_admin": is_admin,
    }

    # 审计日志
    log_id = None
    try:
        result = await session.execute(
            text("INSERT INTO query_log (username, query_text) VALUES (:u, :q)"),
            {"u": user.username, "q": query.query}
        )
        log_id = result.lastrowid
        await session.commit()
    except Exception:
        pass

    return StreamingResponse(
        service.query(query.query, query.chat_context, perms, log_id),
        media_type="text/event-stream"
    )

