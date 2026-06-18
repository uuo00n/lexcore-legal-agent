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
