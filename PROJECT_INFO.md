# 法智项目完整信息档案

> 最后更新：2026-09-03
> 用途：供 AI 助手快速了解项目全貌，避免每次重新读取全部代码
> 说明：本文是当前代码地图，不替代源码。若本文与源码冲突，以源码和测试为准。

## 项目概述

法智是一个面向中国法律咨询场景的 AI Agent 应用。当前项目已经从早期的“RAG + ReAct demo”升级为更接近企业级 harness 的系统：FastAPI 提供 SSE 对话与后台 API，LangGraph 负责计划驱动的多智能体执行，进程内 Service Layer 承载主链工具，FastMCP 作为独立扩展暴露层，Hybrid RAG 负责法条检索，OpenViking Context Layer 负责上下文组织与路由辅助，可观测、配额、缓存、评测和报告链路已经接入。

核心能力：

- 法律咨询：基于本地法律法规 RAG 检索回答，并对明确法条引用做检索结果校验。
- 案件分析：Case Analysis Agent 提取事实、时间线、争议焦点和证据缺口，必要时追问关键事实。
- 合同审查：上传合同后生成 Markdown 审查报告，支持同步接口和异步任务。
- 工具服务：本地 DOC 法条检索、得理法规与类案检索、法律对比、风险评估、合同审查、诉讼时效和文书起草。
- 文档上下文：支持 PDF/DOCX/TXT 上传并注入对话。
- 多层记忆：短窗口、摘要、长期语义记忆、用户画像、OpenViking 风格案件工作区。
- 运行治理：LLM Gateway、fallback provider、模型路由、trace、LLM 调用日志、Prometheus 指标、响应缓存、Redis 缓存/突发限流/幂等、每日配额、admin dashboard。
- 评测闭环：retrieval、e2e、context_ab、openviking_ab 多种评测模式。

## 技术栈

| 层级 | 技术 | 说明 |
| --- | --- | --- |
| Web 框架 | FastAPI | HTTP API、静态页面、SSE 流式响应 |
| 前端 | Vanilla JS SPA | `static/index.html` 对话页，`static/admin.html` 后台看板 |
| Agent 编排 | LangGraph StateGraph | Router + Planner + Supervisor + 三类 Specialist + Verifier + Answer Generator |
| 工具协议 | 进程内 Service Layer + MCP | 主链工具直接调用服务；FastMCP 独立暴露同一服务能力 |
| LLM 网关 | `services.gateway.GatewayChatModel` | 记录调用、fallback、token 使用、延迟和失败 |
| 默认主模型 | DeepSeek `deepseek-v4-pro` | `LLM_PROVIDER=deepseek`，OpenAI 兼容接口；可切 `zhipu` / `qwen` / `ollama` |
| 路由模型 | fast/strong/long | `services.model_routing` 根据复杂度、文档长度、工具轮次选择路由 |
| 查询增强 | HyDE + query rewrite | 默认 `deepseek-v4-flash`（`HYDE_BACKEND=openai`）；也支持 HF + LoRA 后端 |
| 向量存储 | Qdrant | `legal_knowledge` 法律索引与 `legal_memory` 长期记忆双 collection |
| Embedding | `bge-small-zh-v1.5` | 本地 sentence-transformers，512 维 |
| Reranker | `bge-reranker-base` | CrossEncoder 精排 |
| 关键词检索 | BM25 | 中文 unigram + bigram 分词 |
| Context Layer | OpenViking + 本地 fallback | Resource / Memory / Skill，`viking://` URI，L0/L1/L2 分层上下文 |
| 持久化 | PostgreSQL + Redis + Qdrant | PostgreSQL 存权威关系数据与 checkpoint，Redis 存热缓存，Qdrant 存向量 |
| 缓存/限流 | Redis | 检索缓存、得理响应缓存、突发限流、会话元数据、幂等标记；挂掉时全部降级，不是主数据库 |
| 评测 | 自研 retrieval metrics + RAGAS | 100 条法律场景数据集 |

## 当前核心链路

### 应用启动

```text
main.lifespan
  -> init_database()                      # PostgreSQL 核心持久化；ping 失败直接抛错拒绝启动
  -> init_operational_store()             # 只绑定连接池并校验 Alembic 迁移结果，从不建表
  -> init_redis()                          # 探活失败只告警，缓存/限流降级运行
  -> init_memory_store()                  # Qdrant legal_memory collection
  -> initialize_rag()                      # 进程内加载向量库、BM25 与混合检索器
  -> checkpoint_scope()                   # PostgreSQL AsyncPostgresSaver；测试可用 MemorySaver
      -> build_graph(checkpointer)
```

所有 SQL DDL 只由 Alembic 负责，应用启动时只校验、不迁移。lifespan **不会**拉起 MCP 进程。

FastMCP 是与 Web 链路平行的独立进程，需要单独启动：

```text
run_mcp.py
  -> initialize_rag()
      -> load_or_build_index(data/laws)
      -> chunk_all_laws(data/laws)
      -> init_retriever(chunks)
  -> mcp.run(stdio 或 sse)
```

### LangGraph 拓扑

27 个业务节点、19 条静态边、9 组条件边，定义在 `agent/graph.py`：

```text
START
  -> context_compaction
  -> memory
  -> inject_doc
  -> query_rewrite
  -> fact_merge                          # 合并用户补充的事实（澄清续跑）
  -> intent_router
  -> fact_analysis                       # should_after_fact_analysis
       -> clarification -> END           # 个案结论 + 事实不足：中断补问
       -> complexity_router              # should_after_complexity
            -> supervisor                # simple：固定两步计划，跳过 planner
            -> planner -> supervisor     # medium / complex
  supervisor                             # should_execute_next
       -> case_analysis_agent            # should_continue
            -> case_analysis_tools -> collect_case_evidence -> case_analysis_agent
            -> tool_limit_exceeded -> supervisor
            -> supervisor
       -> statute_retrieval_agent        # 同构：statute_retrieval_tools -> collect_statute_evidence
       -> case_retrieval_agent           # 同构：case_retrieval_tools -> collect_case_retrieval_evidence（按需）
       -> legal_consult_agent            # 同构：legal_consult_tools -> collect_consult_evidence
       -> result_verifier                # should_after_verifier
            -> repair_router             # 局部修复（最多一轮）  # should_after_repair
                 -> supervisor           # 重开受影响步骤
                 -> answer_generator     # 只重写答案
            -> planner                   # 问题落不到执行单元时才整体 replan（最多一次）
            -> answer_generator -> END
       -> END                            # 计划终止
```

关键点：

- `intent_router` 与 `planner` 负责意图识别和任务拆分，`supervisor` 只推进计划与调度 Specialist。
- `fact_analysis` 只整理事实与缺口，不检索法规；`complexity_router` 定档 simple/medium/complex，
  simple 直接写入「法规检索 → 法律推理」两步计划，不查类案、不进 Planner。
- `case_analysis_agent` 负责案件结构化与事实缺口；`case_retrieval_agent` 是独立的类案检索单元，
  只在 Planner 生成类案步骤或 Repair Router 判定类案证据不足时运行（`needs_case_retrieval`）。
  §四 的规范职责名（`fact_analysis_agent`/`law_retrieval_agent`/`legal_reasoning_agent`）经
  `agent/agent_names.py` 解析到图内节点名，节点名本身不改。
- `contract_agent` 暂未接入默认 Graph，但合同报告 API 和独立 workflow 仍保留。
- `result_verifier` 先跑 Python 确定性引用核验，再由 LLM 补充语义 issue；LLM 不可用时
  `verification_degraded = true`，不抛 500。核验失败优先 `repair_router` 局部修复，
  只重开受影响步骤并保留第一轮已核验证据；最终用户回答由 `answer_generator` 只从
  `verified_evidence` 生成，`answer_score` 由 `services/final_quality.py` 唯一产出。
- 五个预算：`MAX_PLAN_STEPS=6`（`agent/nodes/planner.py`）、`MAX_TOOL_CALLS_PER_AGENT=2`
  与 `MAX_TOOL_CALLS_PER_REQUEST=3`（`agent/tool_loop.py`，均可用环境变量覆盖）、
  `MAX_REPAIR_ROUNDS=1`（`agent/repair.py`）、`MAX_AGENT_REPLAN_RETRIES=1`（`agent/replan.py`）。
  工具超限走 `tool_limit_exceeded` 写入观察后回到 `supervisor`，不是直接报错；证据已到量、
  重复检索签名、上一轮零增益属于软停止，Agent 直接用已有证据出报告，不记步骤失败。
  单任务计数在分派步骤、局部修复与整体重排时归零，只有全请求累计值 `tool_call_total` 按请求
  存活；耗尽后同样按软停止处理，且 `repair_router` 不再发起需要重新检索的局部修复。

### 聊天请求链路

```text
POST /api/chat
  -> check_rate_limit(thread_id)          # Redis 固定窗口，不可用时放行
  -> consume_request(thread_id)           # PostgreSQL 每日配额
  -> claim_idempotency(token)             # Redis SET NX EX
  -> upsert_thread() / create_trace() / touch_session()
  -> load_doc(doc_id?)
  -> checkpoint 有历史则直接续跑，否则从 messages 表回放
  -> get_cached_answer()                  # 命中则完全跳过 Graph
  -> graph.astream(state_input, config, stream_mode="updates")
  -> SSE: thought / context_status / tool_start / tool_end / token / error / done
  -> set_cached_answer()
  -> complete_trace()
  -> BackgroundTasks: extract_and_save_memory()
```

`token` 事件按 4 字符切片发送，中间 `asyncio.sleep(0.02)`；`SELF_TRACED_TOOLS` 里的工具自己记
trace，SSE 层不重复记。

### RAG 检索链路

```text
Specialist -> retrieve_local_law_tool     # 进程内直调，不经过 MCP Client
  -> services.search.search_local_law_service()
      -> Redis 检索缓存（命中即返回）
      -> HybridRetriever.retrieve()
          -> normalize_query()                 # 第10条 -> 第十条
          -> rewrite_query()                   # 面向 BM25
          -> generate_hypothetical_doc()       # 面向语义检索
          -> SemanticRetriever(hyde_doc + 原始 query + 重写 query)
          -> KeywordRetriever(原始 query + 重写 query)
          -> _drop_superseded()                # RETRIEVAL_INCLUDE_SUPERSEDED=false 时过滤旧法
          -> reciprocal_rank_fusion_scored(k=RRF_K)
          -> Reranker(original query)
          -> score threshold + RETRIEVAL_MIN_RESULTS 兜底
      -> SearchServiceResult(status=found|no_relevant_result|low_quality|error)
```

融合层有 `rrf` / `vector_fallback` / `bm25_fallback` / `empty` 四种模式；Reranker 不可用时退回融合
序。trace 事件依次为 `vector_hits`、`bm25_hits`、`fused_hits`、`reranker_hits`、`rag_summary`。

注意：OpenViking Context Layer 是上下文路由和处理流程辅助，不是法条引用来源。最终明确法条引用必须来自本轮法律检索工具（`retrieve_local_law_tool` / `search_law_tool`）的结果。

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
│   ├── threads.py                  # 会话列表、历史、上下文快照、手动压缩、删除
│   ├── reports.py                  # 合同报告与异步任务
│   ├── evidence.py                 # 视频证据抽帧与产物下载
│   └── admin.py                    # 后台 trace/LLM/eval/quota API
├── agent/
│   ├── graph.py                    # LangGraph 19 节点拓扑与条件边
│   ├── graph_runtime.py            # 节点观测包装（trace / 事件）
│   ├── nodes/                      # context、memory、document、query、routing、planner、supervisor、verifier、answer
│   ├── agents/                     # case_analysis / statute_retrieval / legal_consult / contract
│   ├── tools/                      # 3 个 Agent 工具，直调进程内 Service Layer
│   ├── tool_loop.py                # ReAct 预算与软停止（单任务 + 全请求上限、query_signature）
│   ├── replan.py                   # replan 次数预算（MAX_AGENT_REPLAN_RETRIES）
│   ├── node_utils.py               # 节点公共工具
│   ├── reports.py                  # 合同审查报告组装
│   ├── state.py                    # AgentState TypedDict 与 reducer
│   ├── prompts.py                  # 系统提示词、记忆模板、OpenViking 上下文模板
│   └── skills/                     # 可复用技能包（文书起草、PDF、视频截图、欠薪工作流）
├── mcp_server/
│   ├── server.py                   # FastMCP 实例和工具注册
│   ├── startup.py                  # RAG 初始化
│   └── tools/                      # 10 个 MCP 工具，薄封装 services/
├── services/
│   ├── llm.py                      # provider 工厂，deepseek/zhipu/qwen/ollama
│   ├── gateway.py                  # LLM Gateway，观测和 fallback
│   ├── model_routing.py            # fast/strong/long 路由策略
│   ├── supervisor.py               # Supervisor 路由决策
│   ├── search.py                   # 检索 Service Layer 统一入口，返回结构化质量标记
│   ├── legal_tools.py              # 法律对比、风险、时效、文书模板实现
│   ├── local_legal_retriever.py    # 本地法库检索适配
│   ├── jurisdiction.py             # 管辖判定规则
│   ├── limitations_rules.py        # 诉讼时效规则表
│   ├── legal_analysis.py           # 法律意图、事实、风险、引用、回答质量评分
│   ├── answer_format.py            # 回答展示格式清理
│   ├── context_builder.py          # 分层 token 预算构造模型输入
│   ├── context_compaction.py       # 长会话压缩与 context_status
│   ├── checkpoint.py               # PostgreSQL/MemorySaver checkpoint + 文档入口
│   ├── memory.py                   # PostgreSQL conversation_summaries、user_profiles
│   ├── memory_extractor.py         # 后台摘要、长期记忆、画像、OpenViking 案件工作区
│   ├── memory_store.py             # Qdrant 长期语义记忆
│   ├── cache/                      # Redis response/retrieval/delilegal/rate_limit/session/idempotency
│   ├── quota.py                    # thread_id 级每日请求/token 配额
│   ├── auth.py                     # 可选 admin API key 校验
│   ├── observability.py            # trace、event、LLM call、eval run 存储
│   ├── metrics.py                  # 轻量 Prometheus text format
│   ├── task_queue.py               # 进程内异步任务队列
│   ├── retry.py                    # 统一重试策略
│   ├── errors.py                   # 领域异常
│   ├── doc_parser.py               # PDF/DOCX/TXT 解析
│   ├── evidence_video.py           # 视频证据抽帧
│   ├── contract_report.py          # 合同审查 Markdown 报告
│   ├── contract_agent/             # 合同审查确定性工作流（分类、清单、打分）
│   ├── case_retrieval.py           # 内置相似法律场景库
│   ├── persistence.py              # 消息归档读写
│   ├── viking_context.py           # 本地 OpenViking-style Context Layer
│   ├── openviking_client.py        # 真实 OpenViking HTTP API adapter
│   ├── openviking_context.py       # 真实 OpenViking 优先，本地 fallback
│   ├── openviking_ingest.py        # 法律 Resource / Skill 导入
│   ├── delilegal/                  # 得理开放平台客户端、schema 与响应归一
│   ├── indexer/                    # 法条分块和索引构建
│   ├── retriever/hyde.py           # HyDE 与问题重写（openai / hf_lora 两种后端）
│   └── rag/                        # retriever、qdrant_store、bm25、fusion、reranker、interfaces、startup
├── infrastructure/
│   ├── database.py                 # 异步引擎与会话工厂
│   ├── operational_store.py        # 运行表读写与迁移校验
│   ├── redis.py                    # Redis 连接、熔断与统一降级入口
│   ├── sanitize.py                 # 日志脱敏 formatter
│   ├── models/                     # SQLAlchemy ORM
│   ├── repositories/               # 仓储层
│   └── migrations/                 # Alembic 版本
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
│   ├── architecture/
│   ├── sequences/
│   ├── report/                     # 项目报告与测试结果
│   ├── refactor/                   # 重构期快照（历史记录，勿改写成现状）
│   ├── openviking-context-layer.md # OpenViking 优化与评测记录
│   └── finetune-qwen-law-sft.md    # HyDE/法律问答 LoRA 文档
├── tests/                          # 82 个 test_*.py（含 unit/integration/services 子目录）
├── data/
│   ├── laws/                       # 70 个法律/补充文本，含 2026-06-17 校验报告
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
| GET | `/api/threads/{thread_id}/context` | 上下文快照与 token 占用 |
| POST | `/api/threads/{thread_id}/compact` | 手动触发上下文压缩 |
| DELETE | `/api/threads/{thread_id}` | 删除会话 |
| POST | `/api/reports/contract` | 同步生成合同审查报告 |
| POST | `/api/reports/contract/tasks` | 异步生成合同审查报告 |
| GET | `/api/reports/{report_id}` | 下载 Markdown 报告 |
| GET | `/api/tasks` | 最近异步任务 |
| GET | `/api/tasks/{task_id}` | 任务状态 |
| POST | `/api/evidence/video/extract` | 视频证据抽帧 |
| GET | `/api/evidence/{evidence_id}` | 证据元数据 |
| GET | `/api/evidence/{evidence_id}/files/{relative_path}` | 下载抽帧产物 |
| GET | `/api/admin/summary` | 后台汇总指标，需要可选 admin 鉴权 |
| GET | `/api/admin/traces` | 最近 Agent trace |
| GET | `/api/admin/traces/{trace_id}` | trace 详情 |
| GET | `/api/admin/traces/{trace_id}/timeline` | trace 时间线 |
| GET | `/api/admin/llm-calls` | LLM 调用日志 |
| GET | `/api/admin/eval-runs` | 评测历史 |
| GET | `/api/admin/eval-trends` | 评测趋势 |
| GET | `/api/admin/quota` | 配额使用 |

Admin API 鉴权：如果设置 `ADMIN_API_KEY`，请求需要 Header `X-Admin-Key`。

## 工具清单

工具逻辑只写在 `services/`。Agent 侧和 FastMCP 侧是两套彼此独立的薄封装，**Web 请求链路不经过
MCP Client**，所以 MCP 是否运行不影响问答可用性。

### Agent 工具（3 个，`agent/tools/`）

| 工具 | 实现 | 可用于 |
| --- | --- | --- |
| `retrieve_local_law_tool` | `services.search.search_local_law_service` → HybridRetriever | 三个 Specialist |
| `search_law_tool` | 得理开放平台正式法规检索 | 三个 Specialist |
| `search_case_tool` | 得理开放平台类案检索 | `case_analysis_agent`、`case_retrieval_agent` |

绑定关系见 `agent/tools/__init__.py`：

```python
LEGAL_CONSULT_TOOLS     = [search_law_tool, retrieve_local_law_tool]
STATUTE_RETRIEVAL_TOOLS = [search_law_tool, retrieve_local_law_tool]
CASE_ANALYSIS_TOOLS     = [search_case_tool, search_law_tool, retrieve_local_law_tool]
```

### MCP 工具（10 个，`mcp_server/tools/`）

面向 Claude Desktop 之类的外部 MCP 客户端，`python run_mcp.py` 单独启动。

| 工具 | 说明 |
| --- | --- |
| `legal_search` | 混合法条检索（本地语料） |
| `search_local_law` | 本地法库检索，返回结构化结果与质量标记 |
| `search_law` | 得理正式法规检索 |
| `search_case` | 得理类案检索 |
| `law_compare` | 法律/条文对比 |
| `risk_assess` | 法律风险评估 |
| `contract_review` | 合同文本审查 |
| `statute_of_limitations` | 诉讼时效计算 |
| `jurisdiction_route` | 管辖法院与立案路径判定 |
| `legal_document_draft` | 法律文书起草 |

## 记忆与上下文系统

当前记忆系统分为几层：

1. **运行时状态**：生产使用 PostgreSQL `AsyncPostgresSaver`；单测可显式使用 `MemorySaver`。
2. **消息归档**：PostgreSQL `messages`，保存完整可恢复对话。
3. **短期摘要**：PostgreSQL `conversation_summaries`，超过滑动窗口或 token 上限后增量摘要。
4. **用户画像**：PostgreSQL `user_profiles`，记录身份、关注领域和偏好。
5. **长期语义记忆**：Qdrant `legal_memory` collection，检索 top 3 相关记忆。
6. **OpenViking Context**：`memory` 节点内 best-effort 调用，真实 OpenViking `find()` 优先；失败时回退 `services.viking_context` 本地 Resource / Memory / Skill 目录，异常只降级不阻断。
7. **案件工作区**：对话结束后写入 `data/viking_context/memory/cases/{thread_id}/`，包含 `.abstract.md`、`.overview.md`、`conversation.md`。

两个容易混淆的窗口常量（`services/memory.py`）：

- `SLIDING_WINDOW_SIZE=8` 与 `MAX_WINDOW_TOKENS=3000` 是**摘要与压缩的边界**，决定保留多少条近期消息不做摘要。
- 真正注入模型的近期消息条数由上下文档位决定：基准 `CONTEXT_RECENT_MESSAGE_COUNT=12`
  （`services/context_builder.py`）按档位放大为 12 / 19 / 30 条，并另受 recent-message token 预算限制。

每次模型调用由 `services.context_builder.build_model_context` 按 system、relevant memory
（含用户画像与 `viking_context`）、conversation summary、current plan、retrieved evidence、
current task、recent messages 分层分配 token。

预算按本轮需要分三档定档（确定性判断，不调用模型），档位与结论写入 `context_build` trace：

| 档位 | 场景 | 输入预算 | 输出预留 | 目标使用量 | 法条 / 类案 Top-N | 近期消息 |
| --- | --- | --- | --- | --- | --- | --- |
| `standard` | 普通法律问答 | 32K | 8K | 16K～32K | 6 / 4 | 12 |
| `complex` | 复杂案件分析（默认档） | 64K | 12K | 32K～64K | 10 / 6 | 19 |
| `long` | 长合同 / 多份证据 / 大量类案 | 128K | 16K | 64K～128K | 15 / 10 | 30 |

三档由 `CONTEXT_INPUT_TOKEN_BUDGET=64000` 与 `CONTEXT_OUTPUT_TOKEN_RESERVE=8000` 按比例推导，
统一被 `CONTEXT_MODEL_MAX_TOKENS=128000` 夹住；升到 `long` 由材料 token 数或类案条数触发。
详见 [Context Engineering 与 Memory](docs/architecture/context-engineering-memory.md#上下文档位)。

OpenViking 上下文只做定位和流程提示，不可作为法条依据。法条依据必须来自本轮 `retrieve_local_law_tool` / `search_law_tool` 的返回。

## 可观测与治理

PostgreSQL 运行表：

- `agent_traces`：一次用户请求的输入、最终回答、状态、耗时、法律分析。
- `agent_events`：图节点、工具、模型路由、OpenViking 命中、引用校验等事件。
- `llm_call_logs`：provider、model、base_url、status、latency、fallback、token usage。
- `eval_runs`：评测运行历史和聚合指标。
- `quota_usage`：每日请求数和 token 数。

精确问题缓存存入 Redis，并设置强制 TTL。

Prometheus 指标：

- `legal_chat_requests_total`
- `legal_chat_latency_ms`
- `legal_llm_calls_total`
- `legal_llm_latency_ms`
- `legal_response_cache_hits_total`
- `legal_response_cache_misses_total`
- `legal_cache_lookups_total{namespace,outcome}`：outcome 为 hit / miss / degraded
- `legal_rate_limit_decisions_total{scope,outcome}`：outcome 为 allow / block / degraded / disabled
- `legal_redis_degraded_total{op}`：Redis 降级次数

工作流指标统一由 `services/workflow_metrics.py` 产出（前缀 `legal_workflow_`）：

- `legal_workflow_node_latency_ms` / `legal_workflow_node_total{node,status}`：每个图节点的耗时与成败
- `legal_workflow_route_total{complexity_level,execution_mode,needs_case_retrieval}`：Complexity Router 定档
- `legal_workflow_clarification_total{outcome,blocking}`：澄清闸门结果
- `legal_workflow_planner_degraded_total`：Planner 兜底次数
- `legal_workflow_tool_calls_total{agent}` / `legal_workflow_agent_tool_calls{agent}`：工具调用量
- `legal_workflow_tool_loop_stopped_total{agent,reason}`：软停止原因（证据到量 / 重复查询 / 零增益）
- `legal_workflow_evidence_kept{kind}` / `legal_workflow_evidence_gain` / `legal_workflow_evidence_dropped_total`：Evidence Normalizer 结果
- `legal_workflow_verification_total{result,degraded}` / `legal_workflow_verification_degraded_total` / `legal_workflow_citations_total{status}`：核验结论与引用状态
- `legal_workflow_repair_total{target}` / `legal_workflow_recovery_total{strategy}`：局部修复与 replan / replan_skipped
- `legal_workflow_answer_total{outcome}` / `legal_workflow_answer_attempts`：答案生成
- `legal_workflow_latency_ms{execution_mode,status}`：整轮工作流耗时

缓存命中同时写入 trace：命中记 `cache_hit`，未命中记 `cache_miss`，被限流记 `rate_limited`，
payload 只有命名空间、摘要 key 与 `degraded` 标记，不含缓存值。

## 环境变量

完整可复制清单见 `.env.example`；下面只列关键项与默认值。

### LLM 与模型路由

```bash
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-pro
DEEPSEEK_API_KEY=sk-xxx

# 可选 provider
ZHIPU_API_KEY=sk-xxx
DASHSCOPE_API_KEY=sk-xxx
LLM_BASE_URL_OVERRIDE=
LLM_FALLBACK_PROVIDERS=zhipu,qwen

# 动态模型路由，可只配部分项
LLM_ROUTE_FAST_PROVIDER=deepseek
LLM_ROUTE_FAST_MODEL=deepseek-v4-flash
LLM_ROUTE_STRONG_PROVIDER=deepseek
LLM_ROUTE_STRONG_MODEL=deepseek-v4-pro
LLM_ROUTE_LONG_PROVIDER=deepseek
LLM_ROUTE_LONG_MODEL=deepseek-v4-pro

# 节点/Agent 专用模型；未设置时按注释里的回退链取值
SUPERVISOR_PROVIDER=deepseek
SUPERVISOR_MODEL=deepseek-v4-flash
PLANNER_PROVIDER=deepseek                     # 回退 SUPERVISOR_PROVIDER
PLANNER_MODEL=deepseek-v4-flash               # 回退 SUPERVISOR_MODEL
VERIFIER_PROVIDER=deepseek                    # 回退 SUPERVISOR_PROVIDER
VERIFIER_MODEL=deepseek-v4-pro                # 回退 SUPERVISOR_MODEL
ANSWER_GENERATOR_PROVIDER=deepseek            # 回退 VERIFIER_PROVIDER → SUPERVISOR_PROVIDER
ANSWER_GENERATOR_MODEL=deepseek-v4-pro        # 回退 VERIFIER_MODEL → SUPERVISOR_MODEL
CASE_ANALYSIS_AGENT_PROVIDER=deepseek         # 回退旧名 FACT_AGENT_PROVIDER
CASE_ANALYSIS_AGENT_MODEL=deepseek-v4-pro     # 回退旧名 FACT_AGENT_MODEL
FACT_ANALYSIS_AGENT_PROVIDER=deepseek         # 回退旧名 FACT_AGENT_PROVIDER
FACT_ANALYSIS_AGENT_MODEL=deepseek-v4-flash   # 回退旧名 FACT_AGENT_MODEL
STATUTE_RETRIEVAL_AGENT_PROVIDER=deepseek
STATUTE_RETRIEVAL_AGENT_MODEL=deepseek-v4-pro
CASE_RETRIEVAL_AGENT_PROVIDER=deepseek
CASE_RETRIEVAL_AGENT_MODEL=deepseek-v4-pro
CONTRACT_AGENT_PROVIDER=deepseek
CONTRACT_AGENT_MODEL=deepseek-v4-pro

# 后台摘要 / 长期记忆 / 画像提取用轻量模型，避免抢主问答模型限额
MEMORY_EXTRACTOR_MODEL=deepseek-v4-flash
CONTEXT_COMPACTION_MODEL=deepseek-v4-flash
```

模型 / Provider 的解析口径集中在 `services/model_defaults.py`，节点不再自带模型名。
优先级从高到低：节点专属变量（含上表的多级回退）→ 档位变量 `LLM_ROUTE_{FAST,STRONG,LONG}_*`
→ 全局 `LLM_MODEL` / `LLM_PROVIDER` → 档位内置默认模型（`fast=deepseek-v4-flash`，
`strong`/`long=deepseek-v4-pro`）。空字符串按未配置处理。

档位分配：Supervisor、Planner、Fact Analysis、Case Analysis 的追问分支、Contract 未上传文档时的提示走
`fast`；Result Verifier、Answer Generator、Case Analysis 主分析、Statute Retrieval、
Case Retrieval、Contract 报告摘要走 `strong`；`select_model_route` 在文档超过 6000 字时走 `long`。
Planner 只产出结构化步骤清单，走 `fast` 档省 token；结构化输出失败时已有降级兜底计划，
并记 `legal_workflow_planner_degraded_total`。

主链全部是纯文本推理节点，不要给它们配视觉 / 多模态模型（名字含 `vision`、`-vl`、`vl-`）；
视觉模型只用于识别用户上传的图片与扫描件。误配时 `services.model_defaults` 会打一条 WARNING
提示，但不会自动改写配置。

### RAG 与向量存储

```bash
LAWS_DIR=data/laws
VECTOR_STORE=qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=legal_knowledge
QDRANT_MEMORY_COLLECTION=legal_memory
QDRANT_VECTOR_SIZE=512
QDRANT_MEMORY_VECTOR_SIZE=512
QDRANT_TIMEOUT=5
EMBEDDING_MODEL=models/bge-small-zh-v1.5
RERANKER_MODEL=models/bge-reranker-base
MODEL_DEVICE=cpu

# 三路召回各自的 top_k 与最终条数
RETRIEVAL_VECTOR_TOP_K=10
RETRIEVAL_BM25_TOP_K=10
RETRIEVAL_FINAL_TOP_K=5
RERANKER_TOP_N=5
RERANKER_SCORE_THRESHOLD=0.3
# 精排分全低于阈值时至少保留几条，避免空结果无从归因
RETRIEVAL_MIN_RESULTS=1
# 历史版本/已废止条文是否参与召回；默认关闭
RETRIEVAL_INCLUDE_SUPERSEDED=false
RRF_K=60
MAX_TOOL_CALLS_PER_AGENT=2
MAX_TOOL_CALLS_PER_REQUEST=3
EVIDENCE_LAW_TARGET=5
EVIDENCE_CASE_TARGET=3
EVIDENCE_GAIN_STOP_THRESHOLD=0
```

### 查询增强与 LoRA

```bash
HYDE_ENABLED=true
HYDE_REWRITE_ENABLED=true
HYDE_BACKEND=openai
HYDE_MODEL=deepseek-v4-flash
HYDE_LLM_BASE_URL=https://api.deepseek.com
# 默认复用 DEEPSEEK_API_KEY；需要单独 key 时才配 HYDE_API_KEY
HYDE_API_KEY=

# 可选：改用本地 HuggingFace + LoRA 生成 HyDE
HYDE_BACKEND=hf_lora
HYDE_HF_MODEL_PATH=models/Qwen2.5-7B-Instruct
HYDE_LORA_PATH=models/qwen2_5_hyde_lora
HYDE_HF_MAX_NEW_TOKENS=220
HYDE_HF_TEMPERATURE=0.2
```

### Context Engineering 与 Memory

```bash
# 模型最大允许上下文（取已配置路由中窗口最小的模型），所有档位都被它夹住
CONTEXT_MODEL_MAX_TOKENS=128000
# 单次任务默认输入预算与最终回答预留；三档按比例从这两个基准推导
CONTEXT_INPUT_TOKEN_BUDGET=64000
CONTEXT_OUTPUT_TOKEN_RESERVE=8000
# 升到 long 档的阈值：材料 token 数 / 类案条数
CONTEXT_LONG_MATERIAL_TOKENS=4000
CONTEXT_LONG_CASE_COUNT=8
CONTEXT_RECENT_MESSAGE_COUNT=12
CONTEXT_RETRIEVED_LAW_TOP_N=6
CONTEXT_RETRIEVED_CASE_TOP_N=4
# 各层软预算默认按 prompt 预算比例分配；CONTEXT_*_TOKEN_BUDGET 为可选的绝对值覆盖（会关掉档位缩放）
# 单档位覆盖：CONTEXT_TIER_<STANDARD|COMPLEX|LONG>_{INPUT_TOKENS,OUTPUT_RESERVE,RECENT_MESSAGE_COUNT,LAW_TOP_N,CASE_TOP_N}
# 长会话 checkpoint 压缩阈值
CONTEXT_WINDOW_TOKEN_BUDGET=64000
CONTEXT_AUTO_COMPACT_RATIO=0.75
CONTEXT_AUTO_COMPACT_MESSAGES=40
CONTEXT_COMPACT_KEEP_RECENT=30
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
OPENVIKING_CONTEXT_TIMEOUT=3
OPENVIKING_CONTEXT_SCORE_THRESHOLD=0.45
OPENVIKING_CONTEXT_FALLBACK_LOCAL=true
OPENVIKING_CONTEXT_SKILL_DOMAIN_FILTER=true
OPENVIKING_WORKSPACE=.runtime/openviking/workspace
VIKING_CONTEXT_ROOT=data/viking_context
```

### API、存储、治理

```bash
UPLOAD_DIR=data/uploads
MAX_UPLOAD_MB=10

# PostgreSQL 是硬依赖；不可用或未迁移时应用拒绝启动
DATABASE_URL=postgresql+asyncpg://legal:change-me@localhost:5432/legal
CHECKPOINT_BACKEND=postgres
LANGGRAPH_STRICT_MSGPACK=true

RESPONSE_CACHE_ENABLED=true
RESPONSE_CACHE_TTL_SECONDS=3600
RESPONSE_CACHE_DOC_TTL_SECONDS=300

# Redis：缓存 / 限流 / 会话元数据 / 幂等；留空即全部降级运行
REDIS_URL=redis://localhost:6379/0
RETRIEVAL_CACHE_TTL_SECONDS=1800
DELILEGAL_CACHE_TTL_SECONDS=3600
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
SESSION_METADATA_TTL_SECONDS=86400
IDEMPOTENCY_TTL_SECONDS=600

LEGAL_DAILY_REQUEST_LIMIT=200
LEGAL_DAILY_TOKEN_LIMIT=200000

ADMIN_API_KEY=
DELILEGAL_BASE_URL=https://platform.delilegal.com
DELILEGAL_API_KEY=
DELILEGAL_LAW_SEARCH_PATH=/api/v1/generice/law/list
DELILEGAL_CASE_SEARCH_PATH=/api/v1/generice/case/list
```

## 当前数据状态

- 法律语料：`data/laws` 下 70 个法律/补充文本。
- 最新语料校验：`data/laws/latest_update_report_2026-06-17.md`，来源为国家法律法规数据库。
- 当前分块：`chunk_all_laws("data/laws")` 可生成 8941 个 `LawChunk`。
- 评测数据集：`eval/dataset.json` 共 100 条法律场景。
- 测试文件：`tests/` 下 82 个 `test_*.py`（含 `unit/`、`integration/`、`services/` 子目录）。
- PostgreSQL：Alembic 管理业务、文档、摘要、画像、配额和可观测性表。
- Qdrant：首次初始化后包含 `legal_knowledge` 与 `legal_memory` 两个 collection。
- 本地模型：`models/bge-small-zh-v1.5` 和 `models/bge-reranker-base` 已作为默认 embedding/reranker 路径。

OpenViking 评测阶段性结论见 `docs/openviking-context-layer.md`：法条级 Resource 粒度是正确方向，但全量 OpenViking boost 尚未稳定证明检索提升；后续重点是领域过滤、历史版本降权、score 融合和阈值控制。

## 启动命令

以下命令都在仓库根目录执行；示例使用 Windows PowerShell，macOS/Linux 把
`.venv\Scripts\Activate.ps1` 换成 `source .venv/bin/activate` 即可。

### 依赖服务

```powershell
docker compose up -d postgres redis qdrant
alembic upgrade head
```

PostgreSQL 是硬依赖：DSN 不通或迁移未执行时应用会直接拒绝启动。Redis 缺省不影响启动。

### Web 服务

```powershell
.venv\Scripts\Activate.ps1
uvicorn main:app --host 0.0.0.0 --port 8000 --reload `
  --loop services.checkpoint:selector_event_loop_factory
```

`--loop` 是 Windows 上的必需项：`AsyncPostgresSaver` 依赖 psycopg 的 async 连接，
默认的 ProactorEventLoop 不兼容，必须切到 SelectorEventLoop。

访问：

```text
http://localhost:8000/
http://localhost:8000/admin
http://localhost:8000/metrics
```

### MCP Server

**不会**被 FastAPI lifespan 自动拉起，需要单独启动：

```powershell
.venv\Scripts\Activate.ps1
python run_mcp.py
```

SSE 模式：

```powershell
$env:MCP_TRANSPORT="sse"; $env:MCP_SSE_PORT="8001"; python run_mcp.py
```

### 评测

```powershell
.venv\Scripts\Activate.ps1

python eval/run_eval.py --mode retrieval
python eval/run_eval.py --mode context_ab --limit 10 --fast
python eval/run_eval.py --mode openviking_ab --limit 10 --top-k 5
python eval/run_eval.py --mode e2e
python eval/run_eval.py --mode all
```

`e2e` 在进程内调用 `initialize_rag()` 与 `build_graph(checkpointer=None)`，不需要启动 FastAPI，
也不需要 MCP Server 或 PostgreSQL——只要 Qdrant 里已有 `legal_knowledge` 索引即可。

### OpenViking 语料导入

```powershell
.venv\Scripts\Activate.ps1

python scripts/import_openviking_corpus.py --laws --skills --wait
python scripts/import_openviking_corpus.py --article-cards --skills --wait-after-import
```

### 法律语料刷新

```powershell
.venv\Scripts\Activate.ps1
python scripts/update_laws_from_flk.py
```

### 测试

```powershell
.venv\Scripts\Activate.ps1
pytest -q
```

针对关键模块：

```powershell
pytest tests/test_supervisor.py tests/test_supervisor_nodes.py -q
pytest tests/test_openviking_context.py tests/test_openviking_ab_eval.py -q
pytest tests/test_gateway.py tests/test_model_routing.py tests/test_observability.py -q
pytest tests/test_cache.py tests/test_quota.py tests/test_reports_api.py -q
```

## 关键设计决策

1. **Service Layer 单一实现**：检索与法律工具的逻辑只写在 `services/`，`agent/tools/` 与
   `mcp_server/tools/` 都是薄封装。Web 链路进程内直调，不经过 MCP Client，因此 MCP 是否运行不影响问答。
2. **Plan-and-Execute 而非单层 ReAct**：Router 判意图、Planner 拆计划、Supervisor 逐步分派、
   Verifier 校验、Answer Generator 成稿；Router/Planner/Verifier 与格式化步骤保持确定性节点。
3. **有界执行**：计划步数（6）、单任务工具调用次数（2）、局部修复轮次（1）、replan 次数（1）、上下文 token 都有显式预算，
   超限走 `tool_limit_exceeded` 记录观察后继续，而不是抛错终止。
4. **事实不足先追问**：短且缺核心事实的法律问题先补事实，降低错误法律结论风险。
5. **OpenViking 是 Context Layer**：用于 Resource / Memory / Skill 定位和 trace 可解释，不替代
   LangGraph、Qdrant 或法条检索工具；`memory` 节点里 best-effort 调用，失败只降级。
6. **法条引用必须可校验**：最终回答中的明确法条引用会与本轮检索结果比对，不支持的引用会被移除或提示。
7. **LLM Gateway 统一治理**：所有模型调用经 `GatewayChatModel` 记录延迟、错误、fallback 和 token usage。
8. **精确响应缓存**：只缓存完全归一化后的问题 + doc_id，避免法律场景中模糊缓存误复用。带 doc_id 的回答用独立短 TTL，不长期缓存合同相关正文。
9. **Redis 只放可丢弃的热数据**：检索缓存、得理响应缓存、限流、会话元数据、幂等标记全部经统一降级入口（`infrastructure/redis.py`），Redis 挂掉时限流与幂等 fail-open、缓存退化为重算，Agent 主链照常运行；key 只放摘要，不含提问、合同正文与凭据。
10. **权威记录在 PostgreSQL**：配额、trace、消息归档以关系库为准；所有 DDL 只由 Alembic 负责，
    应用启动只校验迁移结果、从不建表。
11. **配额先按 thread_id 做 subject**：当前没有登录系统，后续可替换为 user_id。
12. **异步任务队列是进程内版本**：适合第一版合同报告任务；服务重启会丢失任务状态，生产级需替换 PostgreSQL/RQ/Celery。
13. **Checkpoint 与长期 Memory 分工**：AsyncPostgresSaver 只解决中断恢复与线程连续性，可被压缩、
    过期或删除；长期 Memory 存在 Qdrant `legal_memory`，按 `user_id` 隔离。两者不能互相替代。
14. **评测驱动迭代**：检索、上下文路由、真实 OpenViking 和端到端回答都应进入 eval，而不是只靠手测。

## 给后续 AI 的注意事项

- 不要把项目描述成旧版单 Agent ReAct 系统；当前是 Router + Planner + Supervisor + 三类 Specialist。
- 不要说 Web 请求会经过 MCP，或说 MCP Server 由 FastAPI lifespan 启动；`main.py` 从不拉起 MCP 进程，
  `agent/tools/` 是进程内直调。
- 不要把默认模型写成智谱；默认 provider 是 `deepseek`，HyDE 默认 `deepseek-v4-flash`。
- 不要提 `services/mcp_client.py`、`agent/nodes.py`、`services/vectorstore/`、ChromaDB 或 SQLite——
  这些都已不存在；向量存储只有 Qdrant，关系库只有 PostgreSQL。
- 不要把 `SLIDING_WINDOW_SIZE`（8）当成模型输入窗口；模型窗口是 `CONTEXT_RECENT_MESSAGE_COUNT`（基准 12，
  按上下文档位放大为 12 / 19 / 30）。
- 不要说上下文预算是一个固定值；它按 standard / complex / long 三档定档，默认档为 complex（64K）。
- 不要把 OpenViking 简化成“数据库替代品”；它在本项目里主要是上下文组织、检索前路由和决策辅助。
- 不要把 OpenViking 命中当成法条来源；法条依据必须来自 `retrieve_local_law_tool` / `search_law_tool`。
- 不要声称已有完整生产级认证；当前只有可选 admin API key，普通用户身份仍以 thread_id 近似。
- 不要声称异步任务持久可靠；`services.task_queue` 是进程内队列。
- 修改 RAG、OpenViking、模型路由或引用校验时，优先补充或更新 eval 和 trace 字段。
- 修改语料后，重新确认 `data/laws/latest_update_report_*.md`、chunk 数量和相关评测结果。
