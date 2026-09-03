# PostgreSQL + Redis + Qdrant 迁移计划

::: tip 这份计划已执行完毕，保留作为迁移决策记录
下面描述的目标状态现在就是实际状态：`0001_initial_schema` 与 `0002_operational_storage` 两个
Alembic 版本已落地，Redis 缓存层 fail-open，Qdrant 的 `legal_knowledge` 与 `legal_memory`
双 collection 已在 `main.py` lifespan 里初始化，旧的嵌入式后端（Chroma、SQLite）连依赖和环境变量
一起删除，并由 `tests/test_storage_architecture.py` 与 `tests/test_docker_setup.py` 把门禁固化下来。

运行口径请以 [PostgreSQL 持久化](../architecture/postgresql-persistence.md)、
[Redis 缓存](../architecture/redis-cache.md) 与 [部署文档](../guide/deployment.md) 为准。
:::

## 目标状态

- PostgreSQL：业务会话、消息、Agent 运行、上传文档、摘要、画像、配额和可观测性历史。
- Redis：检索、外部 API 与精确回答缓存，突发限流、会话热数据和幂等标记。
- Qdrant：`legal_knowledge` 保存法律向量，`legal_memory` 保存长期记忆。
- LangGraph：生产 checkpoint 使用 PostgreSQL；单元测试可显式使用进程内 MemorySaver。
- 关系表只由 Alembic 迁移创建；应用启动仅验证迁移是否完整。

## 初始化顺序

```text
postgres healthy
  -> migrate: alembic upgrade head
  -> app: connect PostgreSQL and validate required tables
  -> connect Redis (fail-open cache layer)
  -> initialize Qdrant legal_memory collection
  -> initialize Qdrant legal_knowledge collection and build missing law index
  -> open AsyncPostgresSaver scope
  -> compile and serve Agent graph
```

PostgreSQL 或迁移不完整时应用拒绝启动，避免写入半初始化 schema。Redis 不可用时缓存与突发
限流降级，但 PostgreSQL 每日配额和 Agent 主链仍可工作。Qdrant 是检索与长期记忆必需组件，
初始化失败时应用拒绝对外服务。

## 实施项

1. `0002_operational` 创建 documents、conversation_summaries、user_profiles、quota_usage、
   llm_call_logs、agent_traces、agent_events、eval_runs。
2. 同步 Agent 节点通过 PostgreSQL Engine 访问运行表；异步业务仓储继续使用 AsyncEngine。
3. 精确回答缓存迁入 Redis，并保留 TTL、哈希 key 和 fail-open 行为。
4. 法律索引固定为 Qdrant；长期记忆改为独立 Qdrant collection，强制 owner/thread 过滤。
5. 删除旧向量实现、嵌入式数据库代码、依赖、环境变量和本地运行目录约定。
6. 增加架构门禁、单元测试、真实 PostgreSQL 迁移测试、Qdrant 双 collection 和 Docker 首启验证。

## 验收门槛

- 生产 Python 源码不存在旧后端 import、路径或环境变量。
- 依赖清单不包含旧后端包或测试驱动。
- 全量 pytest 通过；真实 PostgreSQL 上 Alembic upgrade/check 与运行数据 CRUD 通过。
- 全新 Docker 卷首次启动健康，Qdrant 两个 collection 存在且法律索引数量正确。
- 验证完成后删除测试容器、命名卷和临时 `.env`，确保交付状态没有历史运行数据。
