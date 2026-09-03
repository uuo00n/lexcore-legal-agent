# PostgreSQL 核心持久化

第十七阶段将核心业务数据迁移到 SQLAlchemy 2 Async + PostgreSQL。应用通过
`DATABASE_URL` 创建 asyncpg 连接池，生产启动时默认要求数据库可用。

## 数据边界

PostgreSQL 负责：

- `users`
- `conversations`
- `messages`
- `agent_runs`
- `tool_calls`
- `documents`
- `conversation_summaries`
- `user_profiles`
- `quota_usage`
- `agent_traces` / `agent_events` / `llm_call_logs` / `eval_runs`

上传文档、配额、评测历史、LLM 调用统计、会话摘要和用户画像均已迁入 PostgreSQL；
缓存由 Redis 承担。LangGraph 的 thread-scoped state 默认通过 `AsyncPostgresSaver` 写入 PostgreSQL；
开发和测试可显式设置 `CHECKPOINT_BACKEND=memory` 使用进程内后端。

后台时间线使用的 `agent_events` 与 trace 历史也位于 PostgreSQL；运行状态
的权威记录是 `agent_runs`，工具调用的权威记录是 `tool_calls`。

`agent_runs.plan`、`tool_calls.input`、`tool_calls.output_summary` 以及各模型的结构化
`metadata`/偏好字段在 PostgreSQL 中使用 JSONB。工具输出只写命中数、状态、字段名等
摘要，不写完整检索结果或文书正文。

## 初始化与迁移

先配置 `.env`：

```dotenv
DATABASE_URL=postgresql+asyncpg://legal:change-me@localhost:5432/legal
CHECKPOINT_BACKEND=postgres
LANGGRAPH_STRICT_MSGPACK=true
```

再执行迁移：

```bash
alembic upgrade head
alembic current
alembic check
```

回退迁移会删除业务与运行数据表，仅应在确认数据可丢弃的环境执行：

```bash
alembic downgrade base
```

应用启动只做连接探活和 schema 完整性校验，不自动建表或自动执行迁移。PostgreSQL 是必需组件。

LangGraph checkpoint 表由 `langgraph-checkpoint-postgres` 自带的 schema migration
管理，不进入本项目 Alembic revision。`CHECKPOINT_AUTO_SETUP=true`（默认）会在启动时
幂等调用 `AsyncPostgresSaver.setup()`；严格变更管理的生产环境可以在部署步骤完成
setup 后关闭该选项。checkpointer 默认复用 `DATABASE_URL`，也可设置独立的
`CHECKPOINT_DATABASE_URL=postgresql://...`。

PostgreSQL 状态恢复集成测试需要专用测试库：

```bash
CHECKPOINT_INTEGRATION_DSN=postgresql://legal:test@localhost:5432/legal_test \
python -m pytest -q tests/integration/test_postgres_checkpointer.py
```

## 敏感信息

仓储层是 JSONB Trace 和错误文本的唯一写入口。`api_key`、`secret`、`token`、
`Authorization`、密码、Cookie、DSN 等字段和值会在写入前递归脱敏。可观测性事件
复用同一脱敏器，避免旁路写入泄露凭据。
