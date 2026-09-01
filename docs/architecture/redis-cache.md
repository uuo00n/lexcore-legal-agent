# Redis 缓存 / 限流 / 会话元数据

第十九阶段引入 Redis。Redis **不是长期 Memory 主数据库**，也不是任何数据的唯一副本：
它只承载可以随时丢弃并重建的热数据。权威记录仍然在 PostgreSQL（核心业务表与
LangGraph checkpoint）、SQLite（辅助表）与 Chroma/Qdrant（向量）里。

连接与降级入口在 `infrastructure/redis.py`，五类用途实现在 `services/cache/`。

## 数据边界

| 用途 | 模块 | key 命名空间 | 默认 TTL | Redis 不可用时 |
|------|------|--------------|----------|----------------|
| 检索结果缓存 | `services/cache/retrieval.py` | `legal:cache:retrieval:{v}:{query 摘要}:{参数指纹}` | 1800s | 每次执行完整 Hybrid 检索 |
| 得理 API 响应缓存 | `services/cache/delilegal.py` | `legal:cache:delilegal:{endpoint_type}:{请求体指纹}` | 3600s | 每次请求真实上游 |
| 突发限流 | `services/cache/rate_limit.py` | `legal:ratelimit:{scope}:{window}:{subject 摘要}` | = 窗口长度 | fail-open 放行，由每日配额兜底 |
| 会话元数据热层 | `services/cache/session.py` | `legal:session:{thread_id 摘要}` | 86400s | 读返回空 dict，写为 no-op |
| 幂等标记 | `services/cache/idempotency.py` | `legal:idempotency:{scope}:{token 摘要}` | 600s | 放行（宁可重复执行一次） |

响应缓存（`services/cache/response.py`）刻意留在 SQLite：它是主图前的短路分支，
语义上属于业务数据而不是可丢弃的热缓存。

限流与 `services/quota.py` 互补：配额是「每天多少次 / 多少 token」的业务额度，落在
SQLite 上是权威记录；Redis 限流是「每分钟多少次」的突发保护。Redis 挂掉时只失去突发
保护，每日配额仍然生效。

## 配置

```dotenv
REDIS_URL=redis://localhost:6379/0
# REDIS_ENABLED=true            # 未设置时以 REDIS_URL 是否配置为准
# REDIS_KEY_PREFIX=legal        # 与同实例上的其他应用隔离
# REDIS_SOCKET_TIMEOUT=1.0      # 超时保持 1s 量级：缓存层不允许拖慢主链
# REDIS_CONNECT_TIMEOUT=1.0
# REDIS_MAX_CONNECTIONS=20
# REDIS_BREAKER_COOLDOWN_SECONDS=30

RETRIEVAL_CACHE_ENABLED=true
RETRIEVAL_CACHE_TTL_SECONDS=1800
DELILEGAL_CACHE_ENABLED=true
DELILEGAL_CACHE_TTL_SECONDS=3600
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
SESSION_CACHE_ENABLED=true
SESSION_METADATA_TTL_SECONDS=86400
IDEMPOTENCY_ENABLED=true
IDEMPOTENCY_TTL_SECONDS=600
```

`REDIS_URL` 留空即视为未启用，五类用途全部降级运行，不影响任何接口可用性。连接串可能
带密码，日志与 `/api/health` 一律输出 `mask_dsn()` 脱敏后的地址。

## 降级设计

所有 Redis 访问必须经 `infrastructure.redis.execute()`（异步）或 `execute_sync()`
（同步，供 MCP 子进程内的同步检索链路使用）。两者在客户端缺失、依赖未安装、熔断打开或
命令抛错时返回调用方给定的 `default`，**绝不向上抛异常**。

首次失败会打开熔断器（默认 30s），期间直接降级而不再付连接超时的代价——否则 Redis 宕机
会让每个请求都多等一个 socket timeout。任一命令成功即关闭熔断器。

启动时 `main.py` 只做探活并记录日志，探活失败不阻塞启动。`/api/health` 的 `status`
字段不受 Redis 影响，Redis 状态单独放在 `redis` 字段（`ok` / `degraded` / `disabled` /
`uninitialized`）。

各用途的降级语义按「失败后哪个方向更安全」选择：限流与幂等 fail-open（放行），缓存降级
为未命中（重算），会话元数据降级为空（调用方必须能在没有热层时工作）。

## key 与敏感数据约束

`services/cache/keys.py` 是 key 构造的唯一入口：

- 用户提问、合同正文、会话标题、凭据一律不进 key，只以 sha256 摘要（截取 128 bit）参与。
- `thread_id` 属会话标识，限流与会话元数据同样使用摘要分区。
- 结构化请求体用键排序的紧凑 JSON 指纹，保证同一请求在不同进程得到同一 key。
- 每个写入都带 TTL，没有任何常驻 key。

缓存值同样受约束：检索缓存只回写法条召回结果（公开语料），不回写提问本身；得理缓存只存
上游响应体，凭据在 header 中不进缓存；会话元数据用白名单（`ALLOWED_FIELDS`）过滤，只允许
活跃时间、请求计数、最近 trace_id、是否带文档、provider/model，任何其他键被直接丢弃；
幂等记录只存状态、时间戳和一个非敏感引用（trace_id）。

合同与文书正文不进 Redis。带 `doc_id` 的回答仍走 SQLite 响应缓存，并使用独立的短 TTL
（`RESPONSE_CACHE_DOC_TTL_SECONDS`，默认 300s），避免长期缓存合同相关正文。

## 可观测性

每次缓存查询都经 `services/cache/trace.py` 写入 trace 事件：命中记 `cache_hit`，未命中记
`cache_miss`，payload 只有命名空间、已摘要的 key、backend 和 `degraded` 标记，不含缓存值。
因此 `/api/admin/traces/{trace_id}` 时间线上可以直接看到某一轮命中了哪些缓存。被限流的请求
额外记 `rate_limited` 事件。

Prometheus 指标：

- `legal_cache_lookups_total{namespace, outcome}`，`outcome` 为 `hit` / `miss` / `degraded`
- `legal_rate_limit_decisions_total{scope, outcome}`，`outcome` 为 `allow` / `block` / `degraded` / `disabled`
- `legal_redis_degraded_total{op}`，按操作统计降级次数

`degraded` 与 `miss` 分开统计，因此「Redis 挂了」和「缓存真的没命中」在监控上不会混淆。

## 接口影响

`POST /api/chat` 在配额校验前先做突发限流，超限返回 `429` 并带 `Retry-After` 头。请求可以
携带 `Idempotency-Key` 请求头，重复提交返回 `409`；不带该头则不做幂等拦截，避免把用户真正的
重复提问误判为重放。流正常结束标记 `completed`，异常结束释放标记让重试可以重新抢占，客户端
断开则留给 TTL 自然过期。`DELETE /api/threads/{id}` 会清理该会话的元数据热层。

## 本地验证

```bash
python -m pytest -q tests/test_redis_cache.py
```

测试通过注入替身客户端覆盖降级、key 脱敏、TTL 和 trace 记录，不需要真实 Redis。
需要对真实实例验证时，起一个临时实例并指向它即可：

```bash
redis-server --port 6399 --save '' --appendonly no
REDIS_URL=redis://127.0.0.1:6399/0 python -m pytest -q tests/test_redis_cache.py
```

验证降级路径不需要任何服务：把 `REDIS_URL` 指向一个未监听的端口，接口应照常返回，
`/api/health` 的 `redis` 字段变为 `degraded`。
