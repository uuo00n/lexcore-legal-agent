# POST /api/chat

对话接口，通过 SSE 流式返回智能体的思考过程和最终回答。

## 请求

```
POST /api/chat
Content-Type: application/json
```

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
