"""FastAPI 入口 —— 应用启动、路由注册、检索系统初始化。"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from agent.graph import build_graph
from api import admin, chat, evidence, reports, threads, upload
from infrastructure.database import dispose_database, init_database, ping as ping_database
from infrastructure.redis import (
    dispose_redis,
    init_redis,
    ping as ping_redis,
    redis_enabled,
    redis_status,
)
from infrastructure.sanitize import RedactingFormatter
from services.checkpoint import checkpoint_scope, init_meta_db
from services.llm import current_provider
from services.observability import new_trace_id, trace_context


load_dotenv()
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_log_handler],
)
log = logging.getLogger("legal")


async def _probe_ollama() -> None:
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    base = os.getenv("LLM_BASE_URL_OVERRIDE") or "http://localhost:11434/v1"
    tags_url = base.rstrip("/").rsplit("/v1", 1)[0] + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(tags_url)
            if r.status_code == 200:
                models = [m.get("name") for m in r.json().get("models", [])]
                log.info("Ollama reachable; pulled models: %s", models)
            else:
                log.warning("Ollama responded with status %s at %s", r.status_code, tags_url)
    except Exception as exc:
        log.warning("Ollama probe failed at %s: %s", tags_url, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    函数作用：
        应用生命周期管理 —— 初始化数据库、记忆系统、检索系统、MCP Client 和 LangGraph 图。
    输入参数：
        - app: FastAPI
    输出参数：
        - 未标注
    """
    init_database()
    database_ok = await ping_database()
    if not database_ok:
        required = os.getenv("POSTGRES_REQUIRED", "true").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if required:
            await dispose_database()
            raise RuntimeError(
                "PostgreSQL unavailable; configure DATABASE_URL and run 'alembic upgrade head'"
            )
        log.warning("PostgreSQL 不可用，核心持久化已禁用（POSTGRES_REQUIRED=false）")
        await dispose_database()

    init_meta_db()

    # Redis 只承担缓存、限流、会话元数据与幂等标记，不可用时全链路降级，
    # 因此探活失败只告警，绝不阻塞启动。
    init_redis()
    if redis_enabled():
        if await ping_redis():
            log.info("Redis 可用：缓存、限流、会话元数据与幂等标记已启用")
        else:
            log.warning("Redis 不可用，缓存与限流降级运行（不影响 Agent 主链）")
    else:
        log.info("Redis 未启用（REDIS_URL 未配置），缓存与限流降级运行")

    from services.observability import init_observability_tables
    init_observability_tables()
    from services.quota import init_quota_tables
    init_quota_tables()
    from services.cache import init_cache_tables
    init_cache_tables()

    # 初始化记忆系统表
    from services.memory import init_memory_tables
    init_memory_tables()

    # 初始化长期记忆向量存储（ChromaDB memory collection）
    from services.memory_store import init_memory_store
    init_memory_store()

    # RAG 与 LangGraph 在同一进程内通过 Service Layer 连接；FastMCP 仅是独立暴露层。
    from services.rag.startup import initialize_rag
    initialize_rag()
    try:
        # Checkpointer 连接必须覆盖 compiled graph 的完整生命周期。
        async with checkpoint_scope() as checkpointer:
            app.state.graph = build_graph(checkpointer)

            provider = current_provider()
            log.info("LLM provider: %s", provider)
            if provider == "ollama":
                await _probe_ollama()

            yield
    finally:
        await dispose_redis()
        await dispose_database()


app = FastAPI(title="Legal Agent", lifespan=lifespan)


@app.middleware("http")
async def bind_request_trace(request: Request, call_next):
    """每个 HTTP 请求只生成一个 trace_id，并通过请求状态与响应头传播。"""
    trace_id = new_trace_id()
    request.state.trace_id = trace_id
    started = time.perf_counter()
    status_code = 500
    with trace_context(trace_id=trace_id, node_name="fastapi.request"):
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            # 只记录请求元数据，绝不记录请求体、提示词或上传文档正文。
            log.info(
                "http_request trace_id=%s method=%s path=%s status=%s latency_ms=%s",
                trace_id,
                request.method,
                request.url.path,
                status_code,
                int((time.perf_counter() - started) * 1000),
            )


app.include_router(chat.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(threads.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(evidence.router, prefix="/api")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    """
    函数作用：
        待补充。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    return FileResponse("static/index.html")


@app.get("/admin")
async def admin_page():
    """
    函数作用：
        返回后台可观测性看板页面。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    return FileResponse("static/admin.html")


@app.get("/api/health")
async def health():
    """
    函数作用：
        返回服务健康状态。Redis 只影响缓存与限流，不参与 status 判定，
        因此 Redis 降级时整体仍报 ok，只在 redis 字段体现。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    database_ok = await ping_database()
    return {
        "status": "ok" if database_ok else "degraded",
        "provider": current_provider(),
        "database": "ok" if database_ok else "unavailable",
        "redis": redis_status()["state"],
    }


@app.get("/metrics")
async def metrics():
    """
    函数作用：
        导出 Prometheus 文本指标。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from services.metrics import render_prometheus
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")
