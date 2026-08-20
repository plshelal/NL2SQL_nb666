"""FastAPI entrypoint."""

from __future__ import annotations

import uuid

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers.audit_router import audit_router
from app.api.routers.auth_router import auth_router
from app.api.routers.qa_router import qa_router
from app.api.routers.query_router import query_router
from app.api.routers.review_router import review_router
from app.core.context import request_id_ctx_var
from app.core.lifespan import lifespan

from .config import APP_PORT
from .errors import AppError
from .response import fail

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

OPENAPI_TAGS = [
    {"name": "query", "description": "金融问数接口"},
    {"name": "auth", "description": "认证接口"},
    {"name": "audit", "description": "审计日志接口"},
    {"name": "knowledge", "description": "知识管理(总览/审核/添加)接口"},
]

app = FastAPI(
    title="Finance Data API",
    version="0.1.0",
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)

app.include_router(auth_router, tags=["auth"])
app.include_router(qa_router, tags=["qa"])
app.include_router(query_router, tags=["query"])
app.include_router(audit_router, tags=["audit"])
app.include_router(review_router, tags=["knowledge"])

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def add_request_context_var(request: Request, call_next):
    request_id_ctx_var.set(str(uuid.uuid4()))
    return await call_next(request)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(exc.code, exc.message, request.headers.get("X-Request-Id")),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=fail(
            "VALIDATION_ERROR",
            str(exc),
            request.headers.get("X-Request-Id"),
        ),
    )


@app.exception_handler(ValueError)
async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=fail("BAD_REQUEST", str(exc), request.headers.get("X-Request-Id")),
    )


@app.get("/health", summary="健康检查")
def health() -> dict[str, object]:
    return {"code": 0, "message": "ok", "request_id": None, "data": {"status": "ok"}}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=APP_PORT, reload=False)
