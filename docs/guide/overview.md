# 项目概述

## 简介

法智是一个基于 RAG（检索增强生成）+ Supervisor 多智能体架构的中国法律 AI 助手。系统覆盖 70 部中国法律法规，支持法条检索、类案检索、法律对比、风险评估、合同审查、诉讼时效计算、管辖判定、法律文书起草与视频证据提取等功能。

请求链路不是单个 ReAct Agent，而是一张 19 节点的 LangGraph：Intent Router 判定意图，Planner 生成计划，Supervisor 逐步分派给三个 Specialist，Result Verifier 校验证据，Answer Generator 生成最终回答。

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步 HTTP + SSE 流式响应 |
| 智能体框架 | LangGraph | StateGraph + Plan-and-Execute + 有界 ReAct |
| 主 LLM | DeepSeek `deepseek-v4-pro` | 默认 provider 为 `deepseek`，通过 OpenAI 兼容接口调用；可切 `zhipu` / `qwen` / `ollama` |
| 查询增强 LLM | `deepseek-v4-flash` | HyDE + 问题重写；`HYDE_BACKEND=hf_lora` 可切本地 Qwen + LoRA |
| 关系数据库 | PostgreSQL 17+ | 业务、可观测、配额与 LangGraph checkpoint，**硬依赖** |
| 向量数据库 | Qdrant | `legal_knowledge`（法条）与 `legal_memory`（长期记忆）两个 collection |
| 缓存 / 限流 | Redis | 可缺省，缺省即降级运行 |
| Embedding 模型 | bge-small-zh-v1.5 | 本地 sentence-transformers |
| Reranker 模型 | bge-reranker-base | 本地 cross-encoder 精排 |
| 对外工具协议 | MCP (FastMCP) | 独立进程 `run_mcp.py`，stdio 或 SSE；**不在 Web 请求链路上** |
| 前端 | Vanilla JS SPA | SSE 流式展示 |

## 核心功能

### Agent 工具（3 个）

Specialist 在 ReAct 循环里直接调用进程内 Service Layer，不经过 MCP：

| 工具 | 功能 | 可用于 |
|------|------|--------|
| `retrieve_local_law_tool` | 混合检索本地法条（语义 + BM25 + RRF + Rerank） | 三个 Specialist |
| `search_law_tool` | 得理开放平台正式法规检索 | 三个 Specialist |
| `search_case_tool` | 得理开放平台类案检索 | `case_analysis_agent` |

### MCP 工具（10 个）

`run_mcp.py` 面向 Claude Desktop 之类的外部 MCP 客户端，复用同一套 Service Layer：

| 工具 | 功能 |
|------|------|
| `legal_search` | 混合检索法条（本地语料） |
| `search_local_law` | 本地法库检索，返回结构化结果与质量标记 |
| `search_law` | 得理正式法规检索 |
| `search_case` | 得理类案检索 |
| `law_compare` | 对比两部法律在某主题上的规定 |
| `risk_assess` | 根据事实情况评估法律风险 |
| `contract_review` | 审查合同文本的法律合规性 |
| `statute_of_limitations` | 计算诉讼时效 |
| `jurisdiction_route` | 判定管辖法院与立案路径 |
| `legal_document_draft` | 起草法律文书（起诉状、仲裁申请书、合同） |

### 记忆系统（5 层）

1. **Working Memory** — 当前 `AgentState`：计划、检索证据、校验结论、控制字段
2. **Conversation Memory** — 近期消息窗口（默认 12 条，受 token 预算双重限制）
3. **Summary Memory** — 溢出消息由 LLM 压缩为滚动摘要，存 PostgreSQL `conversation_summaries`
4. **Long-term Memory** — 关键事实存入 Qdrant `legal_memory`，按语义相似度 + 时间衰减检索；用户画像存 PostgreSQL
5. **Persistent Workflow State** — LangGraph checkpoint（`AsyncPostgresSaver`），负责中断恢复与线程连续性

详见 [Context Engineering 与 Memory](../architecture/context-engineering-memory.md)。

## 项目目录结构

```
Legal/
├── main.py                    # FastAPI 入口 + lifespan
├── run_mcp.py                 # FastMCP 入口（独立进程）
├── alembic.ini                # 迁移配置
├── requirements.txt           # 生产依赖
├── .env                       # 环境变量配置
│
├── api/                       # HTTP 路由层
│   ├── chat.py                # POST /api/chat (SSE)
│   ├── upload.py              # POST /api/upload
│   ├── threads.py             # 会话管理、history / context / compact
│   ├── reports.py             # 合同审查报告与异步任务
│   ├── evidence.py            # 视频证据提取
│   └── admin.py               # 可观测后台
│
├── agent/                     # LangGraph 智能体
│   ├── graph.py               # StateGraph 拓扑定义（19 节点）
│   ├── graph_runtime.py       # 节点观测包装
│   ├── state.py               # AgentState 与 reducer
│   ├── nodes/                 # 按职责拆分的节点实现
│   ├── agents/                # 三个 Specialist + 合同 Agent
│   ├── tools/                 # Agent 工具（直连 Service Layer）
│   ├── tool_loop.py           # ReAct 次数预算
│   ├── replan.py              # Replan 次数预算
│   ├── prompts.py             # 系统提示词
│   └── skills/                # 可复用技能包
│
├── services/                  # 业务逻辑层
│   ├── llm.py                 # LLM 工厂（多 Provider + fallback）
│   ├── context_builder.py     # 分层上下文构造
│   ├── context_compaction.py  # 上下文压缩
│   ├── memory.py              # PostgreSQL 摘要与画像
│   ├── memory_extractor.py    # 后台记忆提取
│   ├── memory_store.py        # Qdrant 长期记忆
│   ├── checkpoint.py          # LangGraph 检查点
│   ├── search.py              # 检索 Service Layer 统一入口
│   ├── indexer/               # 法条切块与索引构建
│   ├── rag/                   # 混合检索（Qdrant / BM25 / RRF / Reranker）
│   ├── retriever/hyde.py      # 查询增强
│   ├── delilegal/             # 得理开放平台客户端
│   ├── cache/                 # Redis 六类用途
│   ├── contract_agent/        # 合同审查工作流
│   └── observability.py       # trace / 事件 / 指标
│
├── infrastructure/            # 存储基础设施
│   ├── database.py            # 异步引擎与会话
│   ├── models/                # SQLAlchemy ORM（14 张表）
│   ├── repositories/          # 仓储层
│   ├── redis.py               # Redis 连接与降级
│   └── migrations/            # Alembic 版本
│
├── mcp_server/                # FastMCP 对外暴露层（独立进程）
│   ├── server.py              # FastMCP 实例
│   ├── startup.py             # RAG 初始化
│   └── tools/                 # 10 个 MCP 工具（薄封装 services/）
│
├── static/                    # 前端 SPA + 可观测后台页面
├── data/                      # 数据目录
│   ├── laws/                  # 70 部法律法规原文
│   └── uploads/               # 用户上传文件
├── models/                    # 本地模型权重
├── eval/                      # RAGAS 评测与 A/B 实验
├── docs/                      # VitePress 文档站
└── tests/                     # 82 个测试文件
```
