# POST /api/chat

对话接口，通过 SSE 流式返回智能体的思考过程和最终回答。

## 请求

```
POST /api/chat
Content-Type: application/json
```

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| `Content-Type` | 是 | `application/json` |
| `Idempotency-Key` | 否 | 幂等标识。带上后重复提交同一 key 会返回 `409`；不带则不做幂等拦截，用户重复提问不受影响 |

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `thread_id` | string | 是 | 会话 ID（客户端生成的 UUID） |
| `message` | string | 是 | 用户消息 |
| `doc_id` | string | 否 | 关联的上传文档 ID |

```json
{
  "thread_id": "a1b2c3d4",
  "message": "房东不退押金怎么办？",
  "doc_id": null
}
```

## 响应

返回 `text/event-stream`，包含以下事件类型：

### thought — 思考过程

LLM 在调用工具前的推理分析。

```
event: thought
data: {"content": "正在调用 legal_search 检索相关法律信息..."}
```

### tool_start — 工具调用开始

```
event: tool_start
data: {"name": "legal_search"}
```

### tool_end — 工具调用结果

```
event: tool_end
data: {"name": "legal_search", "output": {"status": "found", "query": "押金退还", "results": [{"law_name": "民法典", "article_no": "第七百一十四条", "text": "..."}]}}
```

### token — 最终回答

逐块流式输出（每块约 4 字符）。

```
event: token
data: 根据《民法典》相关规定...
```

### error — 错误

```
event: error
data: {"message": "LLM 调用超时"}
```

### done — 流结束

```
event: done
data:
```

## 错误响应

SSE 流建立前的校验失败返回普通 JSON 错误：

| 状态码 | 触发条件 | 响应 `detail` |
|--------|----------|---------------|
| `400` | `message` 或 `thread_id` 为空 | 错误说明字符串 |
| `429` | 突发限流（Redis 固定窗口，默认 60 秒内 30 次） | `message`、`limit`、`window_seconds`、`retry_after`；同时返回 `Retry-After` 响应头 |
| `429` | 每日配额超限（SQLite 权威记录） | `message`、`request_count`、`token_count`、`request_limit`、`token_limit` |
| `409` | 带 `Idempotency-Key` 的重复提交 | `message`、`state`（`in_progress` / `completed`） |

限流与幂等都建立在 Redis 上，Redis 不可用时二者自动放行（fail-open），由每日配额继续兜底，
主链不会因为缓存层故障而不可用。限流阈值见 `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`。

## 示例

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "test-001", "message": "劳动合同到期不续签有补偿吗？"}'
```

## 行为说明

- 每个 Specialist 任务最多执行 5 次工具调用（`MAX_TOOL_CALLS` 环境变量控制）
- 最终回答末尾会附加检索到的法条引用（如 `【引用法条】《劳动合同法》第四十六条`）
- 对话结束后在后台异步执行记忆提取，不阻塞响应
- 如果 `doc_id` 有效，文档内容会作为 SystemMessage 注入上下文
- 命中响应缓存或检索缓存时会在 trace 时间线上记 `cache_hit` 事件，被限流则记 `rate_limited`
