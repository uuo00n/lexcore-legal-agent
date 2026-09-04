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
| `evidence_id` | string | 否 | 关联的视频证据 ID，来自 `POST /api/evidence/video/extract` |

```json
{
  "thread_id": "a1b2c3d4",
  "message": "房东不退押金怎么办？",
  "doc_id": null,
  "evidence_id": null
}
```

## 响应

返回 `text/event-stream`，共 7 种事件类型：

### thought — 中间内容

模型在调用工具前的推理分析，以及节点进展说明。

```
event: thought
data: {"content": "先检索本地法条，确认押金退还的请求权基础..."}
```

### context_status — 上下文窗口状态

任一节点输出 `context_status` 时推送，用于前端展示上下文压力与是否发生压缩。

```
event: context_status
data: {"message_count": 6, "compactable_messages": 0, "estimated_tokens": 3120, "token_budget": 64000, "usage_ratio": 0.0488, "should_compact": false}
```

### tool_start — 工具调用开始

Specialist 可调用的工具有 3 个：`retrieve_local_law_tool`（本地 RAG）、`search_law_tool`（得理法规）、
`search_case_tool`（得理类案）。

```
event: tool_start
data: {"name": "retrieve_local_law_tool"}
```

### tool_end — 工具调用结果

```
event: tool_end
data: {"name": "retrieve_local_law_tool", "output": {"status": "found", "query": "押金退还", "results": [{"law_name": "民法典", "article_no": "第七百一十四条", "text": "..."}]}}
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
| `429` | 每日配额超限（PostgreSQL 权威记录） | `message`、`request_count`、`token_count`、`request_limit`、`token_limit` |
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

- 每个 Agent 任务最多执行 2 次工具调用（`MAX_TOOL_CALLS_PER_AGENT` 环境变量控制），超限走 `tool_limit_exceeded` 分支回到 Supervisor；证据到量 / 重复检索 / 零增益按软停止处理，直接用已有证据出报告
- 一次请求内所有 Agent、计划步骤和修复轮累计最多 3 次工具调用（`MAX_TOOL_CALLS_PER_REQUEST`）；耗尽后按软停止处理（`reason=request_budget_exhausted`），且不再发起需要重新检索的局部修复
- Result Verifier 核验失败时优先由 Repair Router 局部修复（最多一轮，只重跑受影响的 Agent 步骤），问题落不到执行单元时才触发一次 replan；预算用尽后仍会进入 `answer_generator` 基于已核验证据输出答案
- 简单问题（如单一法条咨询）走固定两步计划，不进 Planner、不查类案；个案结论所需事实不足时本轮以澄清问题结束，用户在同一 `thread_id` 补充后续跑
- 最终回答只引用本轮核验通过的法条（如 `【引用法条】《劳动合同法》第四十六条`），由已核验证据重新生成，不会出现「引用已移除」之类的内部提示
- 对话结束后在后台异步执行记忆提取，不阻塞响应
- 如果 `doc_id` 有效，文档内容会作为 SystemMessage 注入上下文；`evidence_id` 注入视频证据摘要
- 命中响应缓存或检索缓存时会在 trace 时间线上记 `cache_hit` 事件，被限流则记 `rate_limited`
