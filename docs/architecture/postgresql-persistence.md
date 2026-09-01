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

上传文档、缓存、配额、评测历史、LLM 调用统计、会话摘要和用户画像暂时保留在
SQLite；LangGraph checkpoint 仍使用进程内 `MemorySaver`。这些辅助数据将在后续阶段
按独立迁移计划处理。

后台时间线使用的通用 `agent_events` 与旧版 trace 展示镜像暂时仍在 SQLite；运行状态
的权威记录是 PostgreSQL `agent_runs`，工具调用的权威记录是 `tool_calls`。该镜像只为
保持现有后台页面兼容，写入前使用与 PostgreSQL 相同的脱敏器。

`agent_runs.plan`、`tool_calls.input`、`tool_calls.output_summary` 以及各模型的结构化
`metadata`/偏好字段在 PostgreSQL 中使用 JSONB。工具输出只写命中数、状态、字段名等
摘要，不写完整检索结果或文书正文。

## 初始化与迁移

先配置 `.env`：

```dotenv
DATABASE_URL=postgresql+asyncpg://legal:change-me@localhost:5432/legal
POSTGRES_REQUIRED=true
```

再执行迁移：

```bash
alembic upgrade head
alembic current
alembic check
```

回退初始迁移会删除五张核心表，仅应在确认数据可丢弃的环境执行：

```bash
alembic downgrade base
```

应用启动只做连接探活，不自动建表或自动执行迁移。`POSTGRES_REQUIRED=false` 仅用于
临时开发降级；此时核心持久化被禁用，不能作为生产配置。

## 敏感信息

仓储层是 JSONB Trace 和错误文本的唯一写入口。`api_key`、`secret`、`token`、
`Authorization`、密码、Cookie、DSN 等字段和值会在写入前递归脱敏。旧 SQLite
可观测性事件也复用同一脱敏器，避免过渡期旁路写入泄露凭据。
