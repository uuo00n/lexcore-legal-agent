# 项目概述

## 简介

法智是一个基于 RAG（检索增强生成）+ ReAct Agent 架构的中国法律 AI 助手。系统覆盖 52 部中国法律法规，支持法条检索、法律对比、风险评估、合同审查、诉讼时效计算、法律文书起草等功能。

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步 HTTP + SSE 流式响应 |
| 智能体框架 | LangGraph | StateGraph + ReAct 循环 |
| 工具协议 | MCP (Model Context Protocol) | stdio 传输，FastMCP 服务端 |
| 主 LLM | 智谱 glm-4.7 | 默认 provider 为 `zhipu`，通过 OpenAI 兼容接口调用 |
| 查询增强 LLM | qwen2.5:1.5b | 本地 Ollama，用于 HyDE + 问题重写 |
| 向量数据库 | ChromaDB | 法条向量索引 + 长期记忆存储 |
| Embedding 模型 | bge-small-zh-v1.5 | 本地 sentence-transformers |
| Reranker 模型 | bge-reranker-base | 本地 cross-encoder 精排 |
| 前端 | Vanilla JS SPA | SSE 流式展示 |

## 核心功能

### MCP 工具（7 个）

| 工具 | 功能 |
|------|------|
| `legal_search` | 混合检索法条（语义 + BM25 + RRF + Rerank） |
| `law_compare` | 对比两部法律在某主题上的规定 |
| `risk_assess` | 根据事实情况评估法律风险 |
| `contract_review` | 审查合同文本的法律合规性 |
| `statute_of_limitations` | 计算诉讼时效 |
| `legal_document_draft` | 起草法律文书（起诉状、仲裁申请书、合同） |
| `web_search_fallback` | 本地检索无结果时联网搜索 |

### 记忆系统（4 层）

1. **滑动窗口** — 最近 8 条消息直接发送给 LLM
2. **增量摘要** — 溢出消息由 LLM 压缩为滚动摘要
3. **长期语义记忆** — 关键事实存入 ChromaDB，按语义相似度 + 时间衰减检索
4. **用户画像** — 身份、关注领域等实体信息，存入 SQLite

## 项目目录结构

```
Legal/
├── main.py                    # FastAPI 入口
├── run_mcp.py                 # MCP Server 入口
├── requirements.txt           # 生产依赖
├── .env                       # 环境变量配置
│
├── api/                       # HTTP 路由层
│   ├── chat.py                # POST /api/chat (SSE)
│   ├── upload.py              # POST /api/upload
│   └── threads.py             # 会话管理
│
├── agent/                     # LangGraph 智能体
│   ├── graph.py               # StateGraph 拓扑定义
│   ├── nodes.py               # 节点函数
│   ├── state.py               # AgentState 状态定义
│   ├── prompts.py             # 系统提示词
│   └── tools/                 # Agent 工具（MCP 客户端代理）
│
├── mcp_server/                # MCP Server（子进程）
│   ├── server.py              # FastMCP 实例
│   ├── startup.py             # RAG 初始化
│   ├── tools/                 # 7 个 MCP 工具实现
│   └── knowledge/             # 诉讼时效规则 + 文书模板
│
├── services/                  # 业务逻辑层
│   ├── llm.py                 # LLM 工厂（多 Provider）
│   ├── mcp_client.py          # MCP 客户端
│   ├── memory.py              # SQLite 记忆表
│   ├── memory_extractor.py    # 后台记忆提取
│   ├── memory_store.py        # ChromaDB 长期记忆
│   ├── checkpoint.py          # LangGraph 检查点
│   ├── doc_parser.py          # 文档解析
│   ├── indexer/               # 法条索引构建
│   ├── retriever/             # 检索器（语义/BM25/混合/HyDE）
│   └── vectorstore/           # 向量存储抽象
│
├── static/                    # 前端 SPA
├── data/                      # 数据目录
│   ├── laws/                  # 52 部法律法规原文
│   ├── chroma_db/             # ChromaDB 持久化
│   └── uploads/               # 用户上传文件
├── models/                    # 本地模型文件
└── tests/                     # 测试
```
