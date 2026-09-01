# 系统架构

## 全局架构

```mermaid
graph TB
    subgraph Client["前端 (Vanilla JS SPA)"]
        UI[Web UI]
    end

    subgraph Server["FastAPI 服务"]
        API[API 路由层]
        LG[LangGraph Agent]
        CTX[Context Builder]
        MEM[记忆系统]
    end

    subgraph MCP["MCP Server (子进程)"]
        MCPS[FastMCP]
        RAG[RAG Pipeline]
        KB[知识库]
    end

    subgraph Models["模型层"]
        DS[glm-4.7<br/>主 LLM]
        OL[Ollama qwen2.5:1.5b<br/>查询增强]
        EMB[bge-small-zh-v1.5<br/>Embedding]
        RR[bge-reranker-base<br/>Reranker]
    end

    subgraph Storage["存储层"]
        CHROMA[(ChromaDB)]
        POSTGRES[(PostgreSQL)]
        SQLITE[(SQLite 辅助元数据)]
    end

    UI -->|SSE| API
    API --> LG
    LG --> CTX
    CTX --> MEM
    LG -->|MCP stdio| MCPS
    LG --> MEM
    MCPS --> RAG
    MCPS --> KB
    LG -->|LangChain Tools| DELI[Delilegal Service Layer]
    DELI --> OPENAPI[Delilegal OpenAPI]
    RAG --> EMB
    RAG --> RR
    RAG --> OL
    LG --> DS
    MEM --> CHROMA
    MEM --> SQLITE
    LG -->|Checkpoint| POSTGRES
    RAG --> CHROMA
```

## 数据流

```mermaid
flowchart LR
    A[用户提问] --> B[context_compaction<br/>长会话压缩]
    B --> C[memory_node<br/>加载摘要与相关长期记忆]
    C --> D[Context Builder<br/>分层预算构造模型输入]
    D --> E[Specialist / Tool Loop]
    E -->|工具结果| F[collector<br/>提取并限制 Top-N 证据]
    F --> D
    E -->|专家报告| G[Verifier + Answer Generator]
    G --> H[SSE 响应]
    H --> I[后台归档与长期记忆提取]
```

## 子系统说明

### API 层

FastAPI 提供 RESTful 接口，核心是 `/api/chat` 端点通过 SSE（Server-Sent Events）实现流式响应。支持文件上传和会话管理。

### LangGraph Agent

基于 StateGraph 构建的 ReAct 循环智能体。每轮循环：LLM 决策 → 工具调用 → 结果收集 → 再次决策。最多 6 轮迭代。

### MCP Server

独立子进程，通过 stdio 与主进程通信。拥有 RAG 检索管线和所有工具实现。这种架构将检索逻辑与 Agent 逻辑解耦，便于独立扩展和测试。

### RAG Pipeline

三路检索策略：
- **语义检索**：HyDE 假设文档 → bge-small-zh-v1.5 embedding → ChromaDB ANN
- **关键词检索**：问题重写 → BM25 精确匹配
- **精排**：原始 query → bge-reranker-base cross-encoder

通过 RRF（Reciprocal Rank Fusion）融合语义和关键词结果，再经 Reranker 精排 + 分数阈值过滤。

### 记忆系统

记忆和持久化明确分为五层：

1. Working Memory：当前 `AgentState`；
2. Conversation Memory：`messages`，模型只读取有界近期窗口；
3. Summary Memory：长会话滚动摘要；
4. Long-term Memory：用户相关、值得跨轮保存的信息，使用独立且隔离的向量存储；
5. Persistent Workflow State：PostgreSQL checkpoint，只负责工作流恢复，不等同于长期 Memory。

每次模型调用均由 Context Builder 按 system、relevant memory、conversation summary、recent messages、current plan、retrieved evidence 和 current task 分配 token。详见 [Context Engineering 与 Memory](/architecture/context-engineering-memory)。
