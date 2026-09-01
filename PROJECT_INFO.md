# 法智项目完整信息档案

> 最后更新：2026-06-17
> 用途：供 AI 助手快速了解项目全貌，避免每次重新读取全部代码
> 说明：本文是当前代码地图，不替代源码。若本文与源码冲突，以源码和测试为准。

## 项目概述

法智是一个面向中国法律咨询场景的 AI Agent 应用。当前项目已经从早期的“RAG + ReAct demo”升级为更接近企业级 harness 的系统：FastAPI 提供 SSE 对话与后台 API，LangGraph 负责多智能体路由，MCP Server 承载法律工具，Hybrid RAG 负责法条检索，OpenViking Context Layer 负责上下文组织与路由辅助，可观测、配额、缓存、评测和报告链路已经接入。

核心能力：

- 法律咨询：基于本地法律法规 RAG 检索回答，并对明确法条引用做检索结果校验。
- 事实追问：事实明显不足时，先由 fact agent 追问关键事实。
- 合同审查：上传合同后生成 Markdown 审查报告，支持同步接口和异步任务。
- 工具服务：本地 DOC 法条检索、得理法规与类案检索、法律对比、风险评估、合同审查、诉讼时效和文书起草。
- 文档上下文：支持 PDF/DOCX/TXT 上传并注入对话。
- 多层记忆：短窗口、摘要、长期语义记忆、用户画像、OpenViking 风格案件工作区。
- 运行治理：LLM Gateway、fallback provider、模型路由、trace、LLM 调用日志、Prometheus 指标、响应缓存、每日配额、admin dashboard。
- 评测闭环：retrieval、e2e、context_ab、openviking_ab 多种评测模式。

## 技术栈

| 层级 | 技术 | 说明 |
| --- | --- | --- |
| Web 框架 | FastAPI | HTTP API、静态页面、SSE 流式响应 |
| 前端 | Vanilla JS SPA | `static/index.html` 对话页，`static/admin.html` 后台看板 |
| Agent 编排 | LangGraph StateGraph | Supervisor + fact/contract/legal_consult 多智能体路由 |
| 工具协议 | MCP | FastMCP Server，stdio 子进程，Agent 工具通过 MCP Client 代理调用 |
| LLM 网关 | `services.gateway.GatewayChatModel` | 记录调用、fallback、token 使用、延迟和失败 |
| 默认主模型 | 智谱 `glm-4.7` | `LLM_PROVIDER=zhipu`，OpenAI 兼容接口 |
| 路由模型 | fast/strong/long | `services.model_routing` 根据复杂度、文档长度、工具轮次选择路由 |
| 查询增强 | HyDE + query rewrite | 默认可走智谱 `glm-4.6v`；也支持 HF + LoRA HyDE 后端 |
| 向量存储 | ChromaDB | 法条索引 `law_chunks`、长期记忆 `memory` |
| 预留向量库 | Milvus | `VECTORSTORE_TYPE=milvus` 时使用预留实现 |
| Embedding | `bge-small-zh-v1.5` | 本地 sentence-transformers，384 维 |
| Reranker | `bge-reranker-base` | CrossEncoder 精排 |
| 关键词检索 | BM25 | 中文 unigram + bigram 分词 |
| Context Layer | OpenViking + 本地 fallback | Resource / Memory / Skill，`viking://` URI，L0/L1/L2 分层上下文 |
| 持久化 | PostgreSQL + SQLite + ChromaDB | PostgreSQL 存核心业务数据与 LangGraph checkpoint，SQLite 保留辅助数据，Chroma 存向量 |
| 评测 | 自研 retrieval metrics + RAGAS | 100 条法律场景数据集 |

## 当前核心链路

### 应用启动

```text
main.lifespan
  -> init_meta_db()
  -> init_observability_tables()
  -> init_quota_tables()
  -> init_cache_tables()
  -> init_memory_tables()
  -> init_memory_store()                  # ChromaDB memory collection
  -> start_mcp_client()                   # 启动 run_mcp.py 子进程
  -> checkpoint_scope()                   # PostgreSQL；开发/测试可用 MemorySaver
      -> build_graph()
```

MCP Server 启动时：

```text
run_mcp.py
  -> initialize_rag()
      -> load_or_build_index(data/laws)
      -> chunk_all_laws(data/laws)
      -> init_retriever(chunks)
  -> mcp.run(stdio 或 sse)
```

### LangGraph 拓扑

```text
memory
  -> inject_doc
  -> supervisor_agent
       -> fact_agent
            -> END                 # 如果需要追问事实
            -> legal_consult_agent # 如果事实足够
       -> contract_agent
            -> END
       -> legal_consult_agent
            -> tools
            -> collect_laws
            -> legal_consult_agent
            -> END
```

关键点：

- `supervisor_agent` 只负责路由，不直接回答法律问题。
- `fact_agent` 用确定性事实完整性判断 + LLM 追问话术，避免事实不足时硬答。
- `contract_agent` 基于上传文档生成合同审查报告，不走普通 ReAct 工具循环。
- `legal_consult_agent` 负责普通法律咨询、RAG 工具调用、最终回答和法条引用校验。
- `MAX_TOOL_CALLS` 默认 6，防止 ReAct 循环失控。

### 聊天请求链路

```text
POST /api/chat
  -> consume_request(thread_id)
  -> upsert_thread()
  -> create_trace()
  -> load_doc(doc_id?)
  -> 从 MemorySaver 或 messages_archive 恢复历史
  -> get_cached_answer()
  -> graph.astream(...)
  -> SSE: thought / tool_start / tool_end / token / error / done
  -> set_cached_answer()
  -> complete_trace()
  -> BackgroundTasks: extract_and_save_memory()
```

### RAG 检索链路

```text
legal_search MCP tool
  -> HybridRetriever.retrieve()
      -> normalize_query()                 # 第10条 -> 第十条
      -> rewrite_query()                   # 面向 BM25
      -> generate_hypothetical_doc()       # 面向语义检索
      -> SemanticRetriever(hyde_doc + 原始 query)
      -> KeywordRetriever(原始 query + 重写 query)
      -> RRF fusion
      -> Reranker(original query)
      -> score threshold filter
```

注意：OpenViking Context Layer 是上下文路由和处理流程辅助，不是法条引用来源。最终明确法条引用必须来自本轮 MCP 法律检索工具结果。

## 目录结构

```text
Legal/
├── main.py                         # FastAPI 入口，lifespan 初始化全局依赖
├── run_mcp.py                      # MCP Server 入口，支持 stdio/SSE
├── PROJECT_INFO.md                 # 本文件，项目快速地图
├── requirements.txt                # 生产依赖
├── requirements-dev.txt            # 测试/评测依赖
├── requirements-finetune.txt       # LoRA 微调依赖
├── api/
│   ├── chat.py                     # POST /api/chat，SSE 流式、trace、quota、cache、memory
│   ├── upload.py                   # POST /api/upload，PDF/DOCX/TXT 解析
│   ├── threads.py                  # 会话列表、历史、删除
│   ├── reports.py                  # 合同报告与异步任务
│   └── admin.py                    # 后台 trace/LLM/eval/quota API
├── agent/
│   ├── graph.py                    # LangGraph 多智能体拓扑
│   ├── nodes.py                    # memory、supervisor、fact、contract、legal_consult、collect_laws
│   ├── state.py                    # AgentState TypedDict
│   ├── prompts.py                  # 系统提示词、记忆模板、OpenViking 上下文模板
│   └── tools/                      # Agent 侧 MCP 客户端代理工具
├── mcp_server/
│   ├── server.py                   # FastMCP 实例和工具注册
│   ├── startup.py                  # RAG 初始化
│   ├── tools/                      # MCP 工具实现
│   └── knowledge/                  # 诉讼时效规则和文书模板
├── services/
│   ├── llm.py                      # provider 工厂，zhipu/deepseek/qwen/ollama
│   ├── gateway.py                  # LLM Gateway，观测和 fallback
│   ├── model_routing.py            # fast/strong/long 路由策略
│   ├── supervisor.py               # Supervisor 路由决策
│   ├── legal_analysis.py           # 法律意图、事实、风险、引用、回答质量评分
│   ├── answer_format.py            # 回答展示格式清理
│   ├── mcp_client.py               # MCP stdio client，工具超时和串行锁
│   ├── checkpoint.py               # MemorySaver + SQLite 元数据库
│   ├── memory.py                   # messages_archive、summaries、user_profiles
│   ├── memory_extractor.py         # 后台摘要、长期记忆、画像、OpenViking 案件工作区
│   ├── memory_store.py             # ChromaDB 长期语义记忆
│   ├── cache.py                    # 精确问题响应缓存
│   ├── quota.py                    # thread_id 级每日请求/token 配额
│   ├── observability.py            # trace、event、LLM call、eval run 存储
│   ├── metrics.py                  # 轻量 Prometheus text format
│   ├── task_queue.py               # 进程内异步任务队列
│   ├── contract_report.py          # 合同审查 Markdown 报告
│   ├── case_retrieval.py           # 内置相似法律场景库
│   ├── viking_context.py           # 本地 OpenViking-style Context Layer
│   ├── openviking_client.py        # 真实 OpenViking HTTP API adapter
│   ├── openviking_context.py       # 真实 OpenViking 优先，本地 fallback
│   ├── openviking_ingest.py        # 法律 Resource / Skill 导入
│   ├── indexer/                    # 法条分块和索引构建
│   ├── retriever/                  # semantic、keyword、hybrid、hyde、reranker
│   └── vectorstore/                # Chroma/Milvus 向量存储抽象
├── static/
│   ├── index.html                  # 对话页面
│   ├── app.js
│   ├── style.css
│   ├── admin.html                  # 后台看板
│   ├── admin.js
│   └── admin.css
├── scripts/
│   ├── update_laws_from_flk.py     # 从国家法律法规数据库刷新本地法律文本
│   ├── import_openviking_corpus.py # 导入法律 Resource / Skill 到 OpenViking
│   ├── start_openviking_glm47.py   # 启动 OpenViking 相关本地服务
│   ├── openviking_embedding_server.py
│   ├── render_openviking_config.py
│   ├── prepare_hyde_sft_data.py
│   ├── train_qwen_hyde_lora.py
│   ├── infer_qwen_hyde_lora.py
│   ├── train_qwen_law_sft_lora.py
│   ├── infer_qwen_law_lora.py
│   └── merge_qwen_lora.py
├── eval/
│   ├── dataset.json                # 100 条法律场景
│   ├── run_eval.py                 # retrieval/e2e/context_ab/openviking_ab/all
│   ├── metrics.py                  # hit_rate/mrr/precision/recall
│   ├── context_ab.py               # 本地 Context Layer A/B
│   ├── openviking_ab.py            # 真实 OpenViking A/B
│   └── results/                    # 评测输出
├── docs/
│   ├── guide/                      # VitePress 技术文档
│   ├── api/
│   ├── sequences/
│   ├── openviking-context-layer.md # OpenViking 优化与评测记录
│   └── finetune-qwen-law-sft.md    # HyDE/法律问答 LoRA 文档
├── tests/                          # 32 个 test_*.py
├── data/
│   ├── laws/                       # 70 个法律/补充文本，含 2026-06-17 校验报告
│   ├── chroma_db/                  # ChromaDB 持久化目录
│   ├── docs.sqlite                 # 元数据/trace/quota/cache/memory
│   ├── uploads/                    # 用户上传文件
│   ├── reports/                    # 合同审查报告
│   ├── viking_context/             # 本地 OpenViking-style 案件工作区
│   └── finetune/                   # 微调数据
└── models/
    ├── bge-small-zh-v1.5/
    └── bge-reranker-base/
```

## API 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 前端对话页面 |
| GET | `/admin` | 后台可观测性页面 |
| GET | `/api/health` | 健康检查，返回 provider |
| GET | `/metrics` | Prometheus text metrics |
| POST | `/api/chat` | SSE 流式对话 |
| POST | `/api/upload` | 上传 PDF/DOCX/TXT，默认最大 10MB |
| GET | `/api/threads` | 会话列表 |
| GET | `/api/threads/{thread_id}/history` | 会话历史 |
| DELETE | `/api/threads/{thread_id}` | 删除会话 |
| POST | `/api/reports/contract` | 同步生成合同审查报告 |
| POST | `/api/reports/contract/tasks` | 异步生成合同审查报告 |
| GET | `/api/reports/{report_id}` | 下载 Markdown 报告 |
| GET | `/api/tasks` | 最近异步任务 |
| GET | `/api/tasks/{task_id}` | 任务状态 |
| GET | `/api/admin/summary` | 后台汇总指标，需要可选 admin 鉴权 |
| GET | `/api/admin/traces` | 最近 Agent trace |
| GET | `/api/admin/traces/{trace_id}` | trace 详情 |
| GET | `/api/admin/traces/{trace_id}/timeline` | trace 时间线 |
| GET | `/api/admin/llm-calls` | LLM 调用日志 |
| GET | `/api/admin/eval-runs` | 评测历史 |
| GET | `/api/admin/eval-trends` | 评测趋势 |
| GET | `/api/admin/quota` | 配额使用 |

Admin API 鉴权：如果设置 `ADMIN_API_KEY`，请求需要 Header `X-Admin-Key`。

## MCP 工具

MCP Server 注册 7 个工具：

| 工具 | Agent 代理 | 说明 |
| --- | --- | --- |
| `legal_search` | `legal_search_tool` | 混合法条检索 |
| `law_compare` | `law_compare_tool` | 法律/条文对比 |
| `risk_assess` | `risk_assess_tool` | 法律风险评估 |
| `contract_review` | `contract_review_tool` | 合同文本审查 |
| `statute_of_limitations` | `statute_of_limitations_tool` | 诉讼时效计算 |
| `legal_document_draft` | `legal_document_draft_tool` | 法律文书起草 |
| `legal_search` | `retrieve_local_law_tool` | 本地 DOC 法律 RAG |
| — | `search_law_tool` / `search_case_tool` | 经统一 Service Layer 查询得理 OpenAPI |

## 记忆与上下文系统

当前记忆系统分为几层：

1. **运行时状态**：LangGraph `MemorySaver`，只负责当前进程内 checkpoint。
2. **消息归档**：SQLite `messages_archive`，保存完整可恢复对话。
3. **短期摘要**：SQLite `summaries`，超过滑动窗口或 token 上限后增量摘要。
4. **用户画像**：SQLite `user_profiles`，记录身份、关注领域和偏好。
5. **长期语义记忆**：ChromaDB `memory` collection，检索 top 3 相关记忆。
6. **OpenViking Context**：真实 OpenViking `find()` 优先；失败时回退 `services.viking_context` 本地 Resource / Memory / Skill 目录。
7. **案件工作区**：对话结束后写入 `data/viking_context/memory/cases/{thread_id}/`，包含 `.abstract.md`、`.overview.md`、`conversation.md`。

OpenViking 上下文只做定位和流程提示，不可作为法条依据。法条依据必须来自本轮 `legal_search` 等 MCP 工具返回。

## 可观测与治理

SQLite 表：

- `agent_traces`：一次用户请求的输入、最终回答、状态、耗时、法律分析。
- `agent_events`：图节点、工具、模型路由、OpenViking 命中、引用校验等事件。
- `llm_call_logs`：provider、model、base_url、status、latency、fallback、token usage。
- `eval_runs`：评测运行历史和聚合指标。
- `quota_usage`：每日请求数和 token 数。
- `response_cache`：精确问题缓存。

Prometheus 指标：

- `legal_chat_requests_total`
- `legal_chat_latency_ms`
- `legal_llm_calls_total`
- `legal_llm_latency_ms`
- `legal_response_cache_hits_total`
- `legal_response_cache_misses_total`

## 环境变量

### LLM 与模型路由

```bash
LLM_PROVIDER=zhipu
LLM_MODEL=glm-4.7
ZHIPU_API_KEY=sk-xxx

# 可选 provider
DEEPSEEK_API_KEY=sk-xxx
DASHSCOPE_API_KEY=sk-xxx
LLM_BASE_URL_OVERRIDE=
LLM_FALLBACK_PROVIDERS=deepseek,qwen

# 动态模型路由，可只配部分项
LLM_ROUTE_FAST_PROVIDER=zhipu
LLM_ROUTE_FAST_MODEL=glm-4.7
LLM_ROUTE_STRONG_PROVIDER=zhipu
LLM_ROUTE_STRONG_MODEL=glm-4.7
LLM_ROUTE_LONG_PROVIDER=zhipu
LLM_ROUTE_LONG_MODEL=glm-4.7

# 业务 Agent 专用模型
SUPERVISOR_PROVIDER=zhipu
SUPERVISOR_MODEL=GLM-4.6V
FACT_AGENT_PROVIDER=zhipu
FACT_AGENT_MODEL=GLM-4.6V
CONTRACT_AGENT_PROVIDER=zhipu
CONTRACT_AGENT_MODEL=glm-4.7
```

### RAG 与 MCP

```bash
LAWS_DIR=data/laws
VECTORSTORE_TYPE=chroma
CHROMA_DB_PATH=data/chroma_db
EMBEDDING_MODEL=models/bge-small-zh-v1.5
RERANKER_MODEL=models/bge-reranker-base
RETRIEVER_TOP_K=20
RERANKER_TOP_N=5
RERANKER_SCORE_THRESHOLD=0.3
RRF_K=60
MAX_TOOL_CALLS=4
```

### 查询增强与 LoRA

```bash
HYDE_ENABLED=true
HYDE_REWRITE_ENABLED=true
HYDE_MODEL=glm-4.6v
HYDE_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
HYDE_API_KEY=

# 可选：使用本地 HuggingFace + LoRA 生成 HyDE
HYDE_BACKEND=hf_lora
HYDE_HF_MODEL_PATH=/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct
HYDE_LORA_PATH=/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora
HYDE_HF_MAX_NEW_TOKENS=220
HYDE_HF_TEMPERATURE=0.2
```

### OpenViking

```bash
OPENVIKING_CONTEXT_ENABLED=true
OPENVIKING_BASE_URL=http://localhost:1933
OPENVIKING_API_KEY=
OPENVIKING_TIMEOUT=120
OPENVIKING_RESOURCE_TARGET_URI=viking://resources/laws
OPENVIKING_SKILL_TARGET_URI=
OPENVIKING_CONTEXT_RESOURCE_LIMIT=4
OPENVIKING_CONTEXT_SKILL_LIMIT=3
OPENVIKING_CONTEXT_TIMEOUT=3.0
OPENVIKING_CONTEXT_SCORE_THRESHOLD=
OPENVIKING_CONTEXT_FALLBACK_LOCAL=true
OPENVIKING_CONTEXT_SKILL_DOMAIN_FILTER=true
VIKING_CONTEXT_ROOT=data/viking_context
```

### API、存储、治理

```bash
DOCS_DB=data/docs.sqlite
UPLOAD_DIR=data/uploads
MAX_UPLOAD_MB=10

RESPONSE_CACHE_ENABLED=true
RESPONSE_CACHE_TTL_SECONDS=3600

LEGAL_DAILY_REQUEST_LIMIT=200
LEGAL_DAILY_TOKEN_LIMIT=200000

ADMIN_API_KEY=
DELILEGAL_BASE_URL=https://openapi.delilegal.com
DELILEGAL_APP_ID=
DELILEGAL_SECRET=
DELILEGAL_LAW_SEARCH_PATH=/api/qa/v3/search/queryListLaw
DELILEGAL_CASE_SEARCH_PATH=/api/qa/v3/search/queryListCase
```

## 当前数据状态

- 法律语料：`data/laws` 下 70 个法律/补充文本。
- 最新语料校验：`data/laws/latest_update_report_2026-06-17.md`，来源为国家法律法规数据库。
- 当前分块：`chunk_all_laws("data/laws")` 可生成 8941 个 `LawChunk`。
- 评测数据集：`eval/dataset.json` 共 100 条法律场景。
- 测试文件：`tests/` 下 32 个 `test_*.py`。
- 元数据库：`data/docs.sqlite` 包含 threads、docs、messages_archive、summaries、user_profiles、agent_traces、agent_events、llm_call_logs、eval_runs、quota_usage、response_cache 等表。
- 本地模型：`models/bge-small-zh-v1.5` 和 `models/bge-reranker-base` 已作为默认 embedding/reranker 路径。

OpenViking 评测阶段性结论见 `docs/openviking-context-layer.md`：法条级 Resource 粒度是正确方向，但全量 OpenViking boost 尚未稳定证明检索提升；后续重点是领域过滤、历史版本降权、score 融合和阈值控制。

## 启动命令

### Web 服务

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

访问：

```text
http://localhost:8000/
http://localhost:8000/admin
http://localhost:8000/metrics
```

### MCP Server

通常由 FastAPI lifespan 自动启动。单独调试：

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
python run_mcp.py
```

SSE 模式：

```bash
MCP_TRANSPORT=sse MCP_SSE_PORT=8001 python run_mcp.py
```

### 评测

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate

python eval/run_eval.py --mode retrieval
python eval/run_eval.py --mode context_ab --limit 10 --fast
python eval/run_eval.py --mode openviking_ab --limit 10 --top-k 5
python eval/run_eval.py --mode e2e
python eval/run_eval.py --mode all
```

### OpenViking 语料导入

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate

python scripts/import_openviking_corpus.py --laws --skills --wait
python scripts/import_openviking_corpus.py --article-cards --skills --wait-after-import
```

### 法律语料刷新

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
python scripts/update_laws_from_flk.py
```

### 测试

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
pytest -q
```

针对关键模块：

```bash
pytest tests/test_supervisor.py tests/test_supervisor_nodes.py -q
pytest tests/test_openviking_context.py tests/test_openviking_ab_eval.py -q
pytest tests/test_gateway.py tests/test_model_routing.py tests/test_observability.py -q
pytest tests/test_cache.py tests/test_quota.py tests/test_reports_api.py -q
```

## 关键设计决策

1. **MCP 解耦工具实现**：Agent 只看到 LangChain tools，真实法律工具在 MCP Server 内，便于独立扩展和调试。
2. **Supervisor 多智能体路由**：先判断事实不足、合同审查或普通咨询，避免所有请求都进入同一条 ReAct 路径。
3. **事实不足先追问**：短且缺核心事实的法律问题先补事实，降低错误法律结论风险。
4. **OpenViking 是 Context Layer**：用于 Resource / Memory / Skill 定位和 trace 可解释，不替代 LangGraph、ChromaDB 或法条检索工具。
5. **法条引用必须可校验**：最终回答中的明确法条引用会与本轮检索结果比对，不支持的引用会被移除或提示。
6. **LLM Gateway 统一治理**：所有模型调用经 `GatewayChatModel` 记录延迟、错误、fallback 和 token usage。
7. **精确响应缓存**：只缓存完全归一化后的问题 + doc_id，避免法律场景中模糊缓存误复用。
8. **配额先按 thread_id 做 subject**：当前没有登录系统，后续可替换为 user_id。
9. **异步任务队列是进程内版本**：适合第一版合同报告任务；服务重启会丢失任务状态，生产级需替换 SQLite/RQ/Celery。
10. **MemorySaver 与 SQLite 分工**：MemorySaver 用于运行时 LangGraph 状态，SQLite 用于可恢复归档和治理数据。
11. **评测驱动迭代**：检索、上下文路由、真实 OpenViking 和端到端回答都应进入 eval，而不是只靠手测。

## 给后续 AI 的注意事项

- 不要把项目描述成旧版单 Agent ReAct 系统；当前是 Supervisor + 多业务 Agent。
- 不要把 OpenViking 简化成“数据库替代品”；它在本项目里主要是上下文组织、检索前路由和决策辅助。
- 不要把 OpenViking 命中当成法条来源；法条依据必须来自 MCP 检索工具。
- 不要声称已有完整生产级认证；当前只有可选 admin API key，普通用户身份仍以 thread_id 近似。
- 不要声称异步任务持久可靠；`services.task_queue` 是进程内队列。
- 修改 RAG、OpenViking、模型路由或引用校验时，优先补充或更新 eval 和 trace 字段。
- 修改语料后，重新确认 `data/laws/latest_update_report_*.md`、chunk 数量和相关评测结果。
