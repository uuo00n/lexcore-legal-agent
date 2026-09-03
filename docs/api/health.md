# GET /api/health

健康检查接口，用于监控和负载均衡探测。

## 请求

```
GET /api/health
```

## 响应

```json
{
  "status": "ok",
  "provider": "deepseek",
  "database": "ok",
  "redis": "ok"
}
```

| 字段 | 说明 |
|------|------|
| `status` | 服务状态，正常为 `"ok"`；PostgreSQL 不可用时为 `"degraded"` |
| `provider` | 当前使用的 LLM 提供商（默认 `deepseek`，可切换 `zhipu` / `qwen` / `ollama`） |
| `database` | PostgreSQL 探活结果：`ok` / `unavailable` |
| `redis` | Redis 状态：`ok` / `degraded`（连接失败已熔断）/ `disabled`（未配置 `REDIS_URL`）/ `uninitialized` |

Redis 只承担缓存、限流、会话元数据与幂等标记，不参与 `status` 判定：Redis 降级时整体仍报
`ok`，只在 `redis` 字段体现。详见 [Redis 缓存架构](../architecture/redis-cache.md)。

## 示例

```bash
curl http://localhost:8000/api/health
```
