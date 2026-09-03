# API 总览

## 基础信息

| 项目 | 值 |
|------|------|
| Base URL | `http://localhost:8000` |
| 协议 | HTTP/1.1 |
| 认证 | 对话与上传接口无认证；`/api/admin/*` 在配置 `ADMIN_API_KEY` 后需要 `X-Admin-Key` |
| Content-Type | `application/json`（除文件上传外） |
| Trace | 每个响应都带 `X-Trace-ID`；请求头传入同名字段可复用调用方的 trace ID |

## 端点列表

### 页面与探针

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 对话页 |
| GET | `/admin` | 可观测后台看板 |
| GET | `/api/health` | 健康检查 |
| GET | `/metrics` | Prometheus 文本指标 |

### 对话与上传

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 对话（SSE 流式） |
| POST | `/api/upload` | 上传 PDF / DOCX / TXT 并解析 |

### 会话管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/threads` | 会话列表 |
| GET | `/api/threads/{thread_id}/history` | 会话历史消息 |
| GET | `/api/threads/{thread_id}/context` | 当前上下文预算与压缩状态 |
| POST | `/api/threads/{thread_id}/compact` | 手动触发上下文压缩 |
| DELETE | `/api/threads/{thread_id}` | 删除会话 |

### 报告与异步任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/reports/contract` | 同步生成合同审查报告 |
| POST | `/api/reports/contract/tasks` | 提交异步合同审查任务 |
| GET | `/api/reports/{report_id}` | 下载 Markdown 报告 |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{task_id}` | 单个任务状态 |

### 视频证据

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/evidence/video/extract` | 上传视频并抽取关键帧证据 |
| GET | `/api/evidence/{evidence_id}` | 证据报告 |
| GET | `/api/evidence/{evidence_id}/files/{relative_path}` | 下载证据文件 |

### 可观测后台

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/summary` | 请求数、成功率、平均耗时、LLM 调用汇总 |
| GET | `/api/admin/traces` | trace 列表 |
| GET | `/api/admin/traces/{trace_id}` | trace 详情与事件 |
| GET | `/api/admin/traces/{trace_id}/timeline` | trace 时间线回放 |
| GET | `/api/admin/llm-calls` | LLM 调用日志 |
| GET | `/api/admin/eval-runs` | 评测历史 |
| GET | `/api/admin/eval-trends` | 评测指标趋势 |
| GET | `/api/admin/quota` | 每日配额使用情况 |

## SSE 事件格式

`POST /api/chat` 返回 `text/event-stream`，共 7 种事件：

| 事件 | 数据格式 | 说明 |
|------|----------|------|
| `thought` | `{"content": "..."}` | 节点进展与工具决策的中间内容 |
| `context_status` | `{"message_count", "estimated_tokens", "token_budget", "usage_ratio", "should_compact", ...}` | 上下文窗口使用情况，由节点输出触发 |
| `tool_start` | `{"name": "tool_name"}` | 工具调用开始 |
| `tool_end` | `{"name": "...", "output": ...}` | 工具调用结果 |
| `token` | 纯文本 | 最终回答的流式 token（每片 4 字符） |
| `error` | `{"message": "..."}` | 错误信息 |
| `done` | 空 | 流结束标记 |

示例事件流：

```
event: context_status
data: {"message_count": 6, "estimated_tokens": 3120, "token_budget": 12000, "usage_ratio": 0.26, "should_compact": false}

event: thought
data: {"content": "正在检索相关法条..."}

event: tool_start
data: {"name": "retrieve_local_law_tool"}

event: tool_end
data: {"name": "retrieve_local_law_tool", "output": {"status": "found", "results": [...]}}

event: token
data: 根据

event: token
data: 《民法典》

event: token
data: 第七百

event: done
data:
```
