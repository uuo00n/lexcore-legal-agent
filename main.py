"""FastAPI 入口 —— 应用启动、路由注册、检索系统初始化。"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from agent.graph import build_graph
from api import admin, chat, evidence, reports, threads, upload
from infrastructure.database import dispose_database, init_database, ping as ping_database
from services.checkpoint import init_checkpointer, init_meta_db
from services.llm import current_provider


load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
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
    cp = init_checkpointer()

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

    # 启动 MCP Client（连接 MCP Server 子进程）
    from services.mcp_client import start_mcp_client, stop_mcp_client
    await start_mcp_client()

    # 构建 LangGraph 图
    app.state.graph = build_graph(cp)

    provider = current_provider()
    log.info("LLM provider: %s", provider)
    if provider == "ollama":
        await _probe_ollama()

    yield

    # 关闭 MCP Client
    await stop_mcp_client()
    await dispose_database()


app = FastAPI(title="Legal Agent", lifespan=lifespan)
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
        待补充。
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
