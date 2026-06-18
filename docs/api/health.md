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
  "provider": "zhipu"
}
```

| 字段 | 说明 |
|------|------|
| `status` | 服务状态，正常为 `"ok"` |
| `provider` | 当前使用的 LLM 提供商（`zhipu` / `deepseek` / `qwen` / `ollama`） |

## 示例

```bash
curl http://localhost:8000/api/health
```
