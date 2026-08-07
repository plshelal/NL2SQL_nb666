import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.dependencies import get_meta_session
from app.api.schemas.auth_schema import (
    RegisterRequest, LoginRequest, UserInfo, AuthResponse
)
from app.core.log import logger

auth_router = APIRouter()

# 内存 token 存储 {token: UserInfo}
_token_store: dict[str, UserInfo] = {}


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


async def get_current_user(
    authorization: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_meta_session),
) -> UserInfo:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[7:]
    user = _token_store.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期")
    return user


@auth_router.post("/api/auth/register")
async def register(req: RegisterRequest, session: AsyncSession = Depends(get_meta_session)):
    existing = await session.execute(
        text("SELECT id FROM users WHERE username = :u"), {"u": req.username}
    )
    if existing.fetchone():
        return AuthResponse(code=1, message="用户名已存在")

    await session.execute(
        text("INSERT INTO users (username, password_hash, level, position, org_name, allowed_orgs) VALUES (:u, :p, :l, :pos, :org, :a)"),
        {"u": req.username, "p": _hash_password(req.password), "l": req.level, "pos": req.position, "org": req.org_name, "a": f'["{req.org_name}"]'}
    )
    await session.commit()
    logger.info(f"用户注册: {req.username}, 职级={req.level}, 岗位={req.position}")
    return AuthResponse(message="注册成功")


@auth_router.post("/api/auth/login")
async def login(req: LoginRequest, session: AsyncSession = Depends(get_meta_session)):
    result = await session.execute(
        text("SELECT id, username, level, position, org_name, allowed_orgs, password_hash FROM users WHERE username = :u"),
        {"u": req.username}
    )
    row = result.fetchone()
    if not row or row.password_hash != _hash_password(req.password):
        return AuthResponse(code=1, message="用户名或密码错误")

    user = UserInfo(id=row.id, username=row.username, level=row.level, position=row.position)
    token = secrets.token_hex(32)
    _token_store[token] = user
    logger.info(f"用户登录: {req.username}")
    return AuthResponse(data={"token": token, "user": user.model_dump()})


@auth_router.get("/api/auth/me")
async def me(user: UserInfo = Depends(get_current_user)):
    return AuthResponse(data={"user": user.model_dump()})
