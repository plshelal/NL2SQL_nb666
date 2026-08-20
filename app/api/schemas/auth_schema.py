from pydantic import BaseModel


class RegisterRequest(BaseModel):
    username: str
    password: str
    level: str = "普通员工"
    position: str = "综合管理"
    org_name: str = "A市"


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    id: int
    username: str
    level: str
    position: str
    org_name: str = ""


class AuthResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict | None = None
