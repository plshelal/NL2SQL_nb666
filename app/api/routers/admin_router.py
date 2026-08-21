"""用户管理 API · 仅 admin_total 可访问

功能:列出所有用户(含岗位/机构/权限)、修改用户岗位(改 role_permission + allowed_orgs)、重置密码。
admin_total 自己不可改。
"""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.api.routers.auth_router import get_current_user
from app.core.log import logger

admin_router = APIRouter()
ADMIN = "admin_total"


def _require_admin(user):
    if user.username != ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可访问")


# ---- 指标清单(供前端选择) ----

@admin_router.get("/api/admin/indicators")
async def list_indicators(user=Depends(get_current_user),
                          session: AsyncSession = Depends(get_meta_session)):
    """所有可直接查询的指标(供管理员给用户分配权限时选择)"""
    _require_admin(user)
    from app.clients.mysql_client_manager import dw_mysql_client_manager
    from sqlalchemy import text as _t
    async with dw_mysql_client_manager.session_factory() as dw:
        r = await dw.execute(_t(
            "SELECT DISTINCT index_name FROM index_list WHERE index_name IS NOT NULL ORDER BY index_name"))
        direct = [x.index_name for x in r.fetchall() if x.index_name]
    # 计算指标
    r2 = await session.execute(_t("SELECT term FROM indicator_formula ORDER BY term"))
    computed = [x.term for x in r2.fetchall()]
    return {"direct": direct, "computed": computed}


# ---- 岗位权限映射(供前端联动) ----

@admin_router.get("/api/admin/position-permissions")
async def position_permissions(user=Depends(get_current_user),
                                session: AsyncSession = Depends(get_meta_session)):
    """返回所有岗位→指标权限映射 {岗位: [指标1, 指标2, ...]}"""
    _require_admin(user)
    r = await session.execute(text(
        "SELECT role_name, indicator_name FROM role_permission ORDER BY role_name"))
    out: dict[str, list] = {}
    for x in r.fetchall():
        out.setdefault(x.role_name or "", []).append(x.indicator_name)
    return out


# ---- 用户列表 ----

@admin_router.get("/api/admin/users")
async def list_users(user=Depends(get_current_user),
                     session: AsyncSession = Depends(get_meta_session)):
    """列出所有用户:账密哈希、岗位、机构、权限指标、可查机构"""
    _require_admin(user)
    r = await session.execute(text(
        "SELECT id, username, password_hash, level, position, org_name, allowed_orgs "
        "FROM users ORDER BY id"))
    out = []
    for x in r.fetchall():
        # 该岗位的权限指标
        rp = await session.execute(text(
            "SELECT indicator_name FROM role_permission WHERE role_name = :p"),
            {"p": x.position})
        indicators = [r.indicator_name for r in rp.fetchall()]
        try:
            orgs = json.loads(x.allowed_orgs) if x.allowed_orgs else []
        except Exception:
            orgs = []
        out.append({
            "id": x.id, "username": x.username,
            "password_hash": x.password_hash or "",
            "level": x.level or "", "position": x.position or "",
            "org_name": x.org_name or "", "allowed_orgs": orgs,
            "indicators": indicators,
            "is_self": x.username == ADMIN,
        })
    return out


# ---- 修改用户权限 ----

@admin_router.post("/api/admin/users/{uid}")
async def update_user(uid: int, body=Body(...), user=Depends(get_current_user),
                      session: AsyncSession = Depends(get_meta_session)):
    """修改用户: {position?, orgs?, password?, indicators?}
    position 改了 → 重置该用户 role_permission(删旧+插新)
    orgs 改了 → 更新 allowed_orgs
    password 改了 → 更新 password_hash
    indicators 改了 → 重置 role_permission(注意:这会影响同岗位所有用户)
    """
    _require_admin(user)
    # 取用户
    r = await session.execute(text(
        "SELECT username, position FROM users WHERE id=:i"), {"i": uid})
    row = r.fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    if row.username == ADMIN:
        raise HTTPException(403, "不可修改管理员账号")

    position = body.get("position")
    orgs = body.get("orgs")
    password = body.get("password")
    indicators = body.get("indicators")

    # 改岗位
    if position:
        await session.execute(text(
            "UPDATE users SET position=:p WHERE id=:i"),
            {"p": position, "i": uid})
        logger.info(f"[用户管理] 用户#{uid} 岗位改为 {position}")

    # 改可查机构
    if orgs is not None:
        await session.execute(text(
            "UPDATE users SET allowed_orgs=:o WHERE id=:i"),
            {"o": json.dumps(orgs, ensure_ascii=False), "i": uid})
        logger.info(f"[用户管理] 用户#{uid} 机构改为 {orgs}")

    # 改密码
    if password:
        await session.execute(text(
            "UPDATE users SET password_hash=:h WHERE id=:i"),
            {"h": hashlib.sha256(password.encode()).hexdigest(), "i": uid})
        logger.info(f"[用户管理] 用户#{uid} 密码已重置")

    # 改权限指标(以该用户岗位为 role_name,删除旧的,插入新的)
    if indicators is not None:
        target_position = position or row.position
        await session.execute(text(
            "DELETE FROM role_permission WHERE role_name=:r"),
            {"r": target_position})
        for ind in indicators:
            await session.execute(text(
                "INSERT IGNORE INTO role_permission (role_name, indicator_name) VALUES (:r, :i)"),
                {"r": target_position, "i": ind})
        logger.info(f"[用户管理] 岗位「{target_position}」权限指标重置为 {len(indicators)} 个")

    await session.commit()
    return {"ok": True}


# ---- 删除用户 ----

@admin_router.delete("/api/admin/users/{uid}")
async def delete_user(uid: int, user=Depends(get_current_user),
                      session: AsyncSession = Depends(get_meta_session)):
    _require_admin(user)
    r = await session.execute(text("SELECT username FROM users WHERE id=:i"), {"i": uid})
    row = r.fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    if row.username == ADMIN:
        raise HTTPException(403, "不可删除管理员账号")
    await session.execute(text("DELETE FROM users WHERE id=:i"), {"i": uid})
    await session.commit()
    logger.info(f"[用户管理] 删除用户 {row.username}")
    return {"ok": True}
