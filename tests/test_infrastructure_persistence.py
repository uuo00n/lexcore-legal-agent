"""PostgreSQL 核心持久化集成测试。"""
from __future__ import annotations

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from infrastructure.database import (
    DatabaseSettings,
    dispose_database,
    init_database,
    normalize_dsn,
    session_scope,
)
from infrastructure.models import AgentRun, ToolCall
from infrastructure.sanitize import REDACTED
from services.persistence import (
    append_messages,
    ensure_conversation,
    finish_agent_run,
    list_conversations,
    load_messages,
    record_tool_call,
    start_agent_run,
    update_agent_run,
)


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL 未配置，跳过真实 PostgreSQL 集成测试",
)


@pytest.fixture
async def database(monkeypatch):
    database_name = make_url(TEST_DATABASE_URL).database or ""
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL 必须指向名称以 _test 结尾的专用数据库")
    await dispose_database()
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    config = Config("alembic.ini")
    # Alembic's async env entrypoint owns its event loop.  Run the synchronous
    # command in a worker thread because this fixture itself is async.
    await asyncio.to_thread(command.upgrade, config, "head")
    init_database(DatabaseSettings(dsn=normalize_dsn(TEST_DATABASE_URL)))
    try:
        yield
    finally:
        await dispose_database()
        await asyncio.to_thread(command.downgrade, config, "base")


async def test_conversation_and_message_round_trip(database):
    await ensure_conversation("thread-1", title_seed="劳动合同解除后如何维权")
    count = await append_messages(
        "thread-1",
        [
            {"role": "human", "content": "公司通知我明天离职。"},
            {"role": "ai", "content": "先保留书面解除通知。"},
        ],
    )

    assert count == 2
    assert await load_messages("thread-1") == [
        {"role": "human", "content": "公司通知我明天离职。"},
        {"role": "ai", "content": "先保留书面解除通知。"},
    ]
    rows = await list_conversations()
    assert rows[0]["thread_id"] == "thread-1"
    assert rows[0]["title"] == "劳动合同解除后如何维权"
    assert isinstance(rows[0]["created_at"], int)


async def test_agent_run_and_tool_call_are_structured_and_redacted(database):
    await ensure_conversation("thread-2", title_seed="查询法条")
    run_id = await start_agent_run("trace-1", "thread-2")
    assert run_id is not None

    await update_agent_run(
        "trace-1",
        intent="statute_retrieval",
        plan=[{"step": "检索", "api_key": "sk-1234567890abcdef"}],
    )
    await record_tool_call(
        "trace-1",
        agent_name="statute_retrieval_agent",
        tool_name="search_law_tool",
        input_payload={
            "query": "劳动合同解除",
            "headers": {"Authorization": "Bearer abcdef1234567890"},
        },
        output_summary={"result_count": 3, "secret": "do-not-store"},
        latency_ms=42,
        success=False,
        error="request failed with token=abcdef123456",
    )
    await finish_agent_run(
        "trace-1",
        status="error",
        error="postgresql://legal:s3cret@db:5432/legal unavailable",
    )

    async with session_scope() as session:
        run = (
            await session.execute(select(AgentRun).where(AgentRun.trace_id == "trace-1"))
        ).scalar_one()
        call = (
            await session.execute(select(ToolCall).where(ToolCall.trace_id == "trace-1"))
        ).scalar_one()

        assert run.intent == "statute_retrieval"
        assert run.plan["steps"][0]["api_key"] == REDACTED
        assert "s3cret" not in run.error
        assert run.finished_at is not None
        assert run.updated_at is not None

        assert call.agent_run_id == run.id
        assert call.input_payload["headers"]["Authorization"] == REDACTED
        assert call.output_summary["secret"] == REDACTED
        assert "abcdef123456" not in call.error
        assert call.latency_ms == 42
        assert call.success is False
