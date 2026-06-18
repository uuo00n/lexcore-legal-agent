# API 总览

## 基础信息

| 项目 | 值 |
|------|------|
| Base URL | `http://localhost:8000` |
| 协议 | HTTP/1.1 |
| 认证 | 无（当前版本） |
| Content-Type | `application/json`（除文件上传外） |

## 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/chat` | 对话（SSE 流式） |
| POST | `/api/upload` | 上传文件 |
| GET | `/api/threads` | 获取会话列表 |
| GET | `/api/threads/{id}/history` | 获取会话历史 |
| DELETE | `/api/threads/{id}` | 删除会话 |

## SSE 事件格式

`POST /api/chat` 返回 `text/event-stream`，事件类型：

| 事件 | 数据格式 | 说明 |
|------|----------|------|
| `thought` | `{"content": "..."}` | LLM 思考过程 |
| `tool_start` | `{"name": "tool_name"}` | 工具调用开始 |
| `tool_end` | `{"name": "...", "output": ...}` | 工具调用结果 |
| `token` | 纯文本 | 最终回答的流式 token |
| `error` | `{"message": "..."}` | 错误信息 |
| `done` | 空 | 流结束标记 |

示例事件流：

```
event: thought
data: {"content": "正在调用 legal_search 检索相关法律信息..."}

event: tool_start
data: {"name": "legal_search"}

event: tool_end
data: {"name": "legal_search", "output": {"status": "found", "results": [...]}}

event: token
data: 根据

event: token
data: 《民法典》

event: token
data: 第七百

event: done
data:
```
