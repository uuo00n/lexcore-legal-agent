# 会话管理

## GET /api/threads

获取所有会话列表。

### 响应

```json
{
  "threads": [
    {
      "thread_id": "abc123",
      "title": "房东不退押金怎么办",
      "created_at": "2026-05-28T10:30:00",
      "updated_at": "2026-05-28T10:35:00"
    }
  ]
}
```

### 示例

```bash
curl http://localhost:8000/api/threads
```

---

## GET /api/threads/{thread_id}/history

获取指定会话的消息历史。

### 路径参数

| 参数 | 说明 |
|------|------|
| `thread_id` | 会话 ID |

### 响应

```json
{
  "thread_id": "abc123",
  "messages": [
    {
      "role": "user",
      "content": "劳动合同到期不续签有补偿吗？",
      "name": null
    },
    {
      "role": "assistant",
      "content": "根据《劳动合同法》第四十六条...\n\n---\n【引用法条】\n《劳动合同法》第四十六条",
      "name": null
    }
  ]
}
```

消息角色：`user`、`assistant`、`tool`、`system`

::: tip
系统消息中以 `[USER_DOCUMENT]` 开头的文档注入消息会被过滤，不返回给前端。
:::

### 示例

```bash
curl http://localhost:8000/api/threads/abc123/history
```

---

## GET /api/threads/{thread_id}/context

读取会话当前的上下文窗口使用情况，用于判断是否接近压缩阈值。数据来自 LangGraph
checkpoint 快照，读不到快照时按空消息列表返回。

### 响应

```json
{
  "message_count": 46,
  "compactable_messages": 16,
  "estimated_tokens": 49640,
  "token_budget": 64000,
  "usage_ratio": 0.7756,
  "auto_compact_ratio": 0.75,
  "auto_compact_messages": 40,
  "keep_recent": 30,
  "should_compact": true,
  "thread_id": "abc123"
}
```

| 字段 | 说明 |
|------|------|
| `message_count` | checkpoint 中的消息总数 |
| `compactable_messages` | 保留最近 `keep_recent` 条之外、可被压缩的消息数 |
| `estimated_tokens` | 估算的上下文 token 数 |
| `token_budget` | `CONTEXT_WINDOW_TOKEN_BUDGET`，默认与单次任务输入预算同口径（64K） |
| `usage_ratio` | `estimated_tokens / token_budget` |
| `auto_compact_ratio` | `CONTEXT_AUTO_COMPACT_RATIO`，达到即自动压缩 |
| `auto_compact_messages` | `CONTEXT_AUTO_COMPACT_MESSAGES`，超过即自动压缩 |
| `keep_recent` | `CONTEXT_COMPACT_KEEP_RECENT`，压缩时保留的最近消息数；默认对齐最大上下文档位的近期消息上限（30） |
| `should_compact` | 是否已满足自动压缩条件 |

### 示例

```bash
curl http://localhost:8000/api/threads/abc123/context
```

---

## POST /api/threads/{thread_id}/compact

对指定会话主动执行一次上下文压缩（`force=True`，不受阈值限制）。压缩结果通过
`aupdate_state(..., as_node="context_compaction")` 写回 checkpoint，被压缩的消息用
`RemoveMessage` 移除，滚动摘要与用户画像同步落库。

### 响应

```json
{
  "thread_id": "abc123",
  "compacted": true,
  "context_status": {
    "message_count": 30,
    "estimated_tokens": 21400,
    "token_budget": 64000,
    "usage_ratio": 0.3344,
    "should_compact": false,
    "thread_id": "abc123"
  }
}
```

| 字段 | 说明 |
|------|------|
| `compacted` | 本次是否真的执行了压缩；没有可压缩消息时为 `false` |
| `context_status` | 压缩之后的窗口状态，字段与 `GET /context` 相同 |

### 示例

```bash
curl -X POST http://localhost:8000/api/threads/abc123/compact
```

---

## DELETE /api/threads/{thread_id}

删除指定会话及其所有消息。

### 路径参数

| 参数 | 说明 |
|------|------|
| `thread_id` | 会话 ID |

### 响应

```json
{
  "deleted": "abc123"
}
```

### 示例

```bash
curl -X DELETE http://localhost:8000/api/threads/abc123
```
