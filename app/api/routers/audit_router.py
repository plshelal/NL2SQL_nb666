"""审计日志 API · 仅管理员可访问"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.dependencies import get_meta_session
from app.api.routers.auth_router import get_current_user

audit_router = APIRouter()


@audit_router.get("/api/audit/logs")
async def get_logs(
    session: AsyncSession = Depends(get_meta_session),
    user: dict = Depends(get_current_user),
):
    if user.username != "admin_total":
        raise HTTPException(403, "仅管理员可访问")

    result = await session.execute(text(
        "SELECT id, username, query_text, generated_sql, result_data, is_rejected, created_at FROM query_log ORDER BY id DESC LIMIT 200"
    ))
    rows = result.fetchall()
    return [{
        "id": r.id, "username": r.username, "query": r.query_text,
        "sql": r.generated_sql, "result": str(r.result_data)[:500] if r.result_data else "",
        "rejected": bool(r.is_rejected), "time": str(r.created_at)
    } for r in rows]
