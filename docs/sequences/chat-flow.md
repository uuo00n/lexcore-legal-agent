# 对话流程

## 完整时序图

```mermaid
sequenceDiagram
    participant U as 用户
    participant FE as 前端 (JS)
    participant API as FastAPI
    participant LG as LangGraph
    participant MEM as 记忆系统
    participant MCP as MCP Server
    participant LLM as 主 LLM

    U->>FE: 输入问题
    FE->>API: POST /api/chat (SSE)
    API->>LG: astream(state_input)

    Note over LG: memory_node
    LG->>MEM: 加载用户画像
    LG->>MEM: 检索相关长期记忆
    LG->>MEM: 获取历史摘要
    MEM-->>LG: 记忆上下文

    Note over LG: inject_doc_node
    LG->>LG: 注入上传文档（如有）

    Note over LG: agent_node (第 1 轮)
    LG->>LLM: 系统提示 + 记忆 + 最近 8 条消息
    LLM-->>LG: AIMessage (tool_calls)
    LG-->>API: thought 事件
    API-->>FE: SSE: thought

    Note over LG: ToolNode
    LG->>MCP: call_tool("legal_search", {...})
    LG-->>API: tool_start 事件
    API-->>FE: SSE: tool_start
    MCP-->>LG: 检索结果
    LG-->>API: tool_end 事件
    API-->>FE: SSE: tool_end

    Note over LG: collect_retrieved_laws
    LG->>LG: 从 ToolMessage 提取法条

    Note over LG: agent_node (第 2 轮)
    LG->>LLM: 包含工具结果的消息
    LLM-->>LG: AIMessage (无 tool_calls = 最终回答)
    LG->>LG: 附加法条引用

    LG-->>API: 最终回答
    API-->>FE: SSE: token (逐块)
    API-->>FE: SSE: done

    Note over API: 后台任务
    API->>MEM: 异步记忆提取
    MEM->>LLM: 生成增量摘要
    MEM->>MEM: 提取长期记忆 → ChromaDB
    MEM->>MEM: 更新用户画像 → SQLite
```

## 流程说明

### 1. 记忆加载

每次对话开始时，`memory_node` 从三个来源加载上下文：
- **用户画像**：身份、关注领域（SQLite）
- **长期记忆**：语义检索最相关的 3 条历史记忆（ChromaDB）
- **历史摘要**：之前对话的压缩摘要（SQLite）

### 2. ReAct 循环

Agent 最多执行 6 轮工具调用。每轮：
1. LLM 分析问题，决定调用哪个工具
2. 通过 MCP Client 调用 MCP Server 上的工具
3. 收集工具返回的法条信息
4. LLM 根据工具结果继续推理或给出最终回答

### 3. 流式输出

最终回答按每 4 字符一块通过 SSE 推送，前端实时渲染。

### 4. 后台记忆提取

响应流结束后，`BackgroundTasks` 异步执行记忆提取，不阻塞用户体验。
