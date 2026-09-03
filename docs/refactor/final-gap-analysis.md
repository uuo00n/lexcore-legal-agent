# 最终能力差距分析

- **验收时间**：2026-09-03
- **验收对象**：分支 `codex/refactor-legal-data-sources` 当前工作区
- **验收方式**：逐个模块读源码 + 运行完整测试 + 对 PostgreSQL / Redis / Qdrant 真实容器跑本机探针。**未使用 README 或任何文档作为能力判定依据**，所有结论都可回溯到 `文件:行号` 或一条可复现的命令输出。
- **状态口径**：
  - **已实现**：存在可执行实现，已接入主运行链路，并有测试或真实服务探针佐证。
  - **部分实现**：主要代码已存在且可运行，但节点语义与命名不符，或真实外部依赖未联调。
  - **未实现**：没有实际实现，或只有文档声明与空壳。

## 1. 验收结论

**总体通过。** 37 项能力中 **34 项已实现，3 项部分实现，0 项未实现**。上一版快照列出的两条 P0 阻塞项已全部消除：

- **存储栈完成真实联调。** PostgreSQL 持久化、PostgreSQL Checkpointer、Redis、Qdrant 四项从「部分实现」升级为「已实现」，依据是本次针对真实容器执行的集成测试与探针（见第 4 节），不再是 SQLite / FakeRedis / 内存模式替身。
- **向量库收敛为 Qdrant 单后端。** `services/rag/chroma_store.py` 已删除，`services/rag/__init__.py:40` 对非 `qdrant` 的 `VECTOR_STORE` 直接抛错，不存在「默认跑 Chroma」的旁路。
- **ORM 源码不再被 `.gitignore` 屏蔽。** 规则已锚定为 `/models/`（`.gitignore:24`），`git check-ignore infrastructure/models/base.py` 退出码 1。

仍然保留的 3 项部分实现：

1. **Query Rewrite**：Graph 节点只做规范化，语义改写在 RAG 内部且不回写全局 State——这是命名与职责不一致，不是能力缺失。
2. **得理 Law API / Case API**：客户端、请求构造、响应规范化、重试、缓存与工具接入齐备且 mock transport 测试通过，但 `DELILEGAL_BASE_URL` 未配置，`services/delilegal/config.py:45` 会在真实调用前拦截。没有 endpoint 可打，也不应猜测 endpoint，因此无法在本机完成真实契约验证。

一项交付提醒（不改变能力判定）：本文写作时本分支还有一批 `??` 未跟踪文件，属于「代码已写好但没入库」的交付缺口。
截至 2026-09-03 该缺口已基本闭合，下面按原清单标注当前状态：

- 运行必需：`infrastructure/models/`（8 个 ORM 模块）、`infrastructure/operational_store.py`、
  `infrastructure/migrations/versions/0002_operational_storage.py`。**已提交。** 缺前两项 `main.py` 直接
  import 失败；缺第三项则只有 `0001` 会被 `alembic upgrade head` 应用，应用在 lifespan 校验必需表时拒绝启动。
- 测试门禁：`tests/test_docker_setup.py`、`tests/test_memory_store_qdrant.py`、`tests/test_storage_architecture.py`。
  **已提交**（`ea3543f`）。这三个正是禁止 Chroma / SQLite 回归的断言，漏提交等于门禁失效。
- 部署：`Dockerfile`、`docker-compose.yml`、`docker/`、`.dockerignore`。**已提交**（`ee71c84`）。
- 文档：`docs/refactor/` 下三份记录。**已提交**（`05666f6`），`websearch-removal-analysis.md`
  指向 `current-architecture.md` 的链接因此不再是死链。
- 协作约定：`AGENTS.md`（目录职责、命令与提交规范）。**已提交**（`7c7205b`）。它不影响运行与测试，
  但漏提交会让新协作者检出后拿不到这份约定。
- 不应提交：根目录的 `_diag_retrieval.py` 是临时排查脚本，**当前仍在工作区**，应删除或移入 `scripts/`。

## 2. 逐项验收

| # | 能力 | 状态 | 实际代码确认与差距 | 对应代码位置 |
|---:|---|---|---|---|
| 1 | FastAPI | 已实现 | `lifespan` 内按序初始化数据库、运营存储、Redis、记忆库、RAG 与 checkpointer；PostgreSQL ping 失败直接 `RuntimeError` 拒绝启动（无降级旁路）。注册 6 个路由、trace 中间件、健康检查与 Prometheus `/metrics`，聊天走 SSE。真实容器栈 `/api/health` 返回 `{"status":"ok","database":"ok","redis":"ok"}`。 | `main.py:66`, `main.py:76`, `main.py:126`, `main.py:129`, `main.py:154`, `main.py:189`, `main.py:209`, `api/chat.py:628` |
| 2 | LangGraph StateGraph | 已实现 | `StateGraph(AgentState)` 注册 19 个节点、15 条静态边与 5 组条件边，最终 `compile(checkpointer=...)`。 | `agent/graph.py:39`, `agent/graph.py:46`, `agent/graph.py:144`, `agent/graph.py:151`, `agent/graph.py:203` |
| 3 | Stateful Agent Workflow | 已实现 | 编译期注入 checkpointer；State 覆盖消息、计划、报告、检索证据、引用、重试与记忆；API 按 `thread_id` 运行并可回读快照。 | `main.py:109`, `agent/state.py:249`, `api/chat.py:348`, `api/chat.py:616` |
| 4 | Query Rewrite | 部分实现 | Graph 的 `query_rewrite` 节点只压缩空白，属于 normalization。真正的 LLM 改写与 HyDE 在 `services/retriever/hyde.py`，由 `HybridRetriever` 在多路召回时调用（原查询 / 改写 / 假设文档三路），失败回退原查询。差距：节点名与职责不一致，且语义改写结果不写回 `AgentState.rewritten_query`，顶层链路观测不到。 | `agent/nodes/query.py:11`, `services/rag/retriever.py:131`, `services/retriever/hyde.py:221`, `services/retriever/hyde.py:246`, `services/rag/retriever.py:319`, `services/rag/retriever.py:342` |
| 5 | Intent Router | 已实现 | 独立 `intent_router` 节点先走确定性规则路由，再交 LLM 决策并解析复杂度/工具需求/理由，解析失败有回退。 | `agent/nodes/supervisor.py:278`, `agent/graph.py:53`, `services/supervisor.py:52`, `services/supervisor.py:111`, `services/supervisor.py:139` |
| 6 | Planner | 已实现 | Pydantic 结构化输出限制 1–6 步，`TASK_AGENT_MAP` 强校验任务类型与 Agent 的绑定关系（不匹配即 `ValueError`），模型失败时生成确定性计划。 | `agent/nodes/planner.py:19`, `agent/nodes/planner.py:25`, `agent/nodes/planner.py:57`, `agent/nodes/planner.py:109`, `agent/nodes/planner.py:175` |
| 7 | Plan-and-Execute | 已实现 | `_executor_update` 按 `pending -> running -> completed/failed` 推进，回收步骤报告、识别缺失报告并驱动下一跳；条件边负责继续执行或进入 Verifier。 | `agent/nodes/supervisor.py:49`, `agent/nodes/supervisor.py:79`, `agent/nodes/supervisor.py:89`, `agent/nodes/routing.py:18` |
| 8 | Supervisor | 已实现 | Supervisor 同时是入口路由与确定性计划执行器，每个 Specialist 完成后重新调度；另有 LLM 直答分支处理非法律意图。 | `agent/nodes/supervisor.py:247`, `agent/nodes/supervisor.py:278`, `agent/nodes/supervisor.py:360`, `agent/graph.py:150` |
| 9 | Legal Consultation Agent | 已实现 | 构建分层上下文、动态模型路由、绑定可信法规与本地检索工具，循环结束提交结构化 `AgentReport`。 | `agent/agents/legal_consult_agent.py:230`, `agent/graph.py:107` |
| 10 | Statute Retrieval Agent | 已实现 | 绑定得理法规与本地 RAG 工具，对召回法条做 grounded 筛选后输出 `StatuteReport`。 | `agent/agents/statute_retrieval_agent.py:74`, `agent/graph.py:91` |
| 11 | Case Analysis Agent | 已实现 | 处理事实完整性与追问、结构化案情，按需绑定类案/法条/本地检索工具并提交报告。 | `agent/agents/case_analysis_agent.py:89`, `agent/graph.py:75` |
| 12 | Multi-Agent Collaboration | 已实现 | Planner 把任务分派给 3 个已注册 Specialist，Agent 通过共享 State、检索证据和结构化报告协作，Supervisor 串行编排，Verifier 汇总核验。属于可控顺序协作，不是并行 fan-out。附带发现：`agent/agents/contract_agent.py` 有实现且被 `agent/agents/__init__.py` 导出，但从未 `add_node`，`AssignedAgent` 字面量也不含它，因此不可达（详见第 6 节）。 | `agent/nodes/planner.py:25`, `agent/nodes/supervisor.py:89`, `agent/state.py:22`, `agent/state.py:249` |
| 13 | ReAct Agent Loop | 已实现 | 3 个 Specialist 均可产出 tool calls，图走 `Agent -> ToolNode -> collect_evidence -> Agent` 闭环；超过工具预算跳 `tool_limit_exceeded` 回 Supervisor，失败也生成协议合法的 observation。 | `agent/graph.py:162`, `agent/graph.py:171`, `agent/graph.py:195`, `agent/tool_loop.py:19`, `agent/tool_loop.py:35`, `agent/tool_loop.py:69` |
| 14 | LangChain Tool Calling | 已实现 | 使用 `@tool` + `args_schema`、`bind_tools`、LangGraph `ToolNode`、`InjectedState` 注入 trace，并挂 `handle_tool_error`；不是自定义字符串伪调用。 | `agent/tools/law_search.py:14`, `agent/tools/case_search.py:14`, `agent/tools/case_search.py:51`, `agent/tools/rag_search.py:14`, `agent/graph_runtime.py:10` |
| 15 | 得理 Law API | 部分实现 | 异步 client、认证头、超时、重试、缓存、响应规范化与 Agent/FastMCP 工具接入齐备，mock transport 测试通过。差距：`DELILEGAL_BASE_URL` 未配置，`validate_base_url()` 会在发请求前抛 `DelilegalConfigurationError`，真实响应契约（字段、分页、错误码、空结果）未验证。 | `services/delilegal/client.py:77`, `services/delilegal/client.py:124`, `services/delilegal/client.py:177`, `services/delilegal/config.py:45`, `services/delilegal/normalizers.py:28`, `services/delilegal/normalizers.py:88` |
| 16 | 得理 Case API | 部分实现 | 筛选参数枚举、请求构造、裁判文书压缩与相关条款抽取、异常映射、工具接入齐备，mock transport 测试通过。差距与第 15 项同源：无可用 endpoint，未做真实联调。 | `services/delilegal/client.py:186`, `services/delilegal/normalizers.py:17`, `services/delilegal/normalizers.py:120`, `services/delilegal/processors.py:33`, `services/delilegal/processors.py:88`, `services/delilegal/enums.py:5` |
| 17 | Local RAG | 已实现 | FastAPI 与 FastMCP 启动时加载或构建索引并装配混合检索器，Agent 与 MCP 复用同一 Service Layer。真实 Qdrant 上 `legal_knowledge` 曾观测到 8,941 条向量（与本地 70 个法律文件切分出的 8,941 个 chunk 数一致）。 | `services/rag/startup.py:13`, `services/indexer/builder.py:103`, `services/rag/retriever.py:208`, `agent/tools/rag_search.py:14`, `services/search.py:255` |
| 18 | Structured Legal Chunking | 已实现 | 优先按分编/编/章/节/条/款/项切分，过长叶子才递归切；保存法规层级路径、条号、款项、生效日期、效力状态、来源与内容哈希。 | `services/indexer/chunker.py:55`, `services/indexer/chunker.py:429`, `services/indexer/chunker.py:456`, `services/indexer/chunker.py:487`, `services/indexer/chunker.py:525` |
| 19 | Embedding | 已实现 | 索引构建批量调用 SentenceTransformer，查询侧与记忆库使用同族模型生成归一化向量。真实 Qdrant 上确认 `{'size': 512, 'distance': 'Cosine'}`。 | `services/indexer/builder.py:24`, `services/indexer/builder.py:44`, `services/rag/retriever.py:46`, `services/memory_store.py:140` |
| 20 | Qdrant | 已实现 | 唯一允许的向量后端：非 `qdrant` 直接抛错。支持远程/本地/内存三种 client、collection 幂等创建、15 个 keyword payload 索引、uuid5 point ID、内容哈希去重、MatchValue/MatchAny/Range 过滤、按 ID 删除与健康检查。真实容器往返全部通过。 | `services/rag/__init__.py:40`, `services/rag/qdrant_store.py:34`, `services/rag/qdrant_store.py:79`, `services/rag/qdrant_store.py:103`, `services/rag/qdrant_store.py:122`, `services/rag/qdrant_store.py:198`, `services/rag/qdrant_store.py:227`, `services/rag/qdrant_store.py:267` |
| 21 | BM25 | 已实现 | 启动时用全量 law chunks 建 BM25 检索器并注入混合检索器，参与每次召回；不可用时向量路照常工作。 | `services/rag/bm25.py:12`, `services/rag/bm25.py:28`, `services/rag/startup.py:13`, `services/rag/retriever.py:264` |
| 22 | RRF | 已实现 | 实现带分数与不带分数两版 Reciprocal Rank Fusion，以 `chunk_id` 合并多路有序结果，混合管线实际调用。 | `services/rag/fusion.py:11`, `services/rag/fusion.py:22`, `services/rag/retriever.py:282`, `services/rag/retriever.py:419` |
| 23 | Reranker | 已实现 | CrossEncoder 对 RRF 候选精排，异常时回退融合结果并记录降级事件与 `score_type`。 | `services/rag/reranker.py:13`, `services/rag/reranker.py:28`, `services/rag/retriever.py:459`, `services/rag/retriever.py:474` |
| 24 | Result Verifier | 已实现 | 先做确定性审计（计划完成度、报告齐备、来源存在、结论冲突、失效法条），再用结构化 LLM 补充并接地校验补充意见；首次失败最多触发一次 replan。 | `agent/nodes/verifier.py:233`, `agent/nodes/verifier.py:346`, `agent/nodes/verifier.py:372`, `agent/nodes/verifier.py:390`, `agent/nodes/verifier.py:445`, `agent/nodes/verifier.py:470`, `agent/nodes/routing.py:47` |
| 25 | Citation Verification | 已实现 | 报告里的法条/案例 source 与 `retrieved_laws`/`retrieved_cases` 按 ID、名称、条号、案号匹配，识别凭空生成的引用；Answer 阶段再次剔除不可信案例引用并只对可信来源加脚注。 | `agent/nodes/verifier.py:102`, `agent/nodes/verifier.py:120`, `agent/nodes/verifier.py:154`, `agent/nodes/verifier.py:218`, `agent/nodes/answer.py:44`, `agent/nodes/answer.py:183`, `agent/nodes/answer.py:230` |
| 26 | Answer Generator | 已实现 | 独立节点基于原问题、专家报告、可信法条/案例与核验结果生成答案；模型失败有确定性 fallback，并追加来源脚注、软化绝对化表述与风险提示，之后直接到 `END`。 | `agent/nodes/answer.py:143`, `agent/nodes/answer.py:193`, `agent/nodes/answer.py:224`, `agent/nodes/answer.py:265`, `agent/nodes/answer.py:312`, `agent/graph.py:201` |
| 27 | LangGraph State | 已实现 | `TypedDict` State 用 `add_messages` 加 6 个自定义 reducer，覆盖计划、报告、法条、案例、引用、重试计数、记忆与压缩状态；`PlanStep` 等子结构均有类型定义。 | `agent/state.py:22`, `agent/state.py:144`, `agent/state.py:249`, `agent/state.py:253` |
| 28 | Multi-turn Conversation | 已实现 | 同 `thread_id` 经 checkpointer 恢复消息；checkpoint 缺失时回退 PostgreSQL 消息归档；历史 API 优先 checkpoint 再回退归档。真实 PostgreSQL 上跨 checkpointer 实例的同 `thread_id` 恢复测试通过。 | `api/chat.py:287`, `api/chat.py:348`, `api/threads.py:142`, `api/threads.py:233`, `services/persistence.py:126`, `tests/integration/test_postgres_checkpointer.py:46` |
| 29 | Context Compaction | 已实现 | 每轮 Graph 入口估算 token/消息压力，LLM 汇总旧消息、合并实体画像、保存摘要，并用 `RemoveMessage` 从 State 删除已压缩消息；另提供手动压缩 API（`aupdate_state(..., as_node="context_compaction")`）。 | `agent/graph.py:144`, `agent/nodes/context.py:11`, `services/context_compaction.py:139`, `services/context_compaction.py:169`, `services/context_compaction.py:240`, `services/context_compaction.py:300`, `api/threads.py:266` |
| 30 | PostgreSQL | 已实现 | async SQLAlchemy(asyncpg) engine、事务化 `session_scope`、7 个 ORM 模块、两版 Alembic 迁移、`ping`，以及聊天链路写入会话/消息/AgentRun/ToolCall；`build_engine` 拒绝非 PostgreSQL DSN，DSN 只以 `mask_dsn` 形式出日志。真实容器上完成 `alembic upgrade head` → 断言 → `downgrade base` 全流程与敏感字段脱敏断言。 | `infrastructure/database.py:209`, `infrastructure/database.py:255`, `infrastructure/database.py:342`, `infrastructure/database.py:387`, `infrastructure/models/base.py`, `infrastructure/migrations/versions/0001_initial_schema.py`, `infrastructure/migrations/versions/0002_operational_storage.py`, `services/persistence.py:44`, `services/persistence.py:141`, `services/persistence.py:202` |
| 31 | PostgreSQL Checkpointer | 已实现 | `checkpoint_scope` 用 `AsyncPostgresSaver.from_conn_string()` 覆盖 compiled graph 生命周期，支持 `setup()` 与 pipeline，DSN 解析顺序 `CHECKPOINT_DATABASE_URL → DATABASE_URL → POSTGRES_DSN → DatabaseSettings`；Windows 下在 import 期切换 selector 事件循环策略以适配 psycopg。真实库中已存在 `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/`checkpoint_migrations` 四张表，证明 `setup()` 确实对 PostgreSQL 执行过。 | `services/checkpoint.py:21`, `services/checkpoint.py:95`, `services/checkpoint.py:147`, `services/checkpoint.py:179`, `services/checkpoint.py:184`, `main.py:109` |
| 32 | Redis | 已实现 | 异步/同步 client、命名空间 key、TTL、熔断器与 fail-open `execute` 齐备；上层实现响应缓存（sha256 key，不落明文问题）、原子 Lua 限流（INCR+EXPIRE+TTL 单次往返）、`SET NX EX` 幂等、字段白名单会话热层；主链在 `/api/chat` 实际调用。真实容器往返与断网降级双向验证通过。 | `infrastructure/redis.py:238`, `infrastructure/redis.py:302`, `infrastructure/redis.py:411`, `infrastructure/redis.py:488`, `infrastructure/redis.py:516`, `services/cache/response.py:49`, `services/cache/rate_limit.py:31`, `services/cache/idempotency.py:152`, `services/cache/session.py:26`, `api/chat.py:544`, `api/chat.py:572` |
| 33 | Agent Trace | 已实现 | HTTP 中间件生成并回传 `X-Trace-ID`；`ContextVar` 传递 trace 上下文，Graph 节点、模型调用、工具调用、检索、Verifier 与最终答案统一写标准化事件，可按 trace 查询时间线；写入前做 `redact`，不落 prompt/请求体/文档原文。 | `main.py:129`, `services/observability.py:21`, `services/observability.py:79`, `services/observability.py:168`, `services/observability.py:223`, `services/observability.py:256`, `services/observability.py:433`, `agent/graph_runtime.py:28`, `services/rag/retriever.py:166` |
| 34 | Tool Retry | 已实现 | 统一 `RetryPolicy` + tenacity，`is_retryable_exception` 沿异常链判定 transient/permanent；得理 HTTP、RAG 两路召回实际使用，Agent 层 replan 计数与传输层重试分开，互不放大。 | `services/retry.py:19`, `services/retry.py:53`, `services/retry.py:114`, `services/retry.py:126`, `services/delilegal/client.py:124`, `services/rag/retriever.py:290`, `agent/nodes/verifier.py:445` |
| 35 | Exception Handling | 已实现 | `LegalServiceError` 体系带 `retryable` 语义，得理侧再细分认证/超时/上游/响应非法/配置缺失；`ToolException` 转成协议合法的结构化 observation；Redis、检索、精排、记忆与可观测性均为显式降级而非静默失败。 | `services/errors.py:5`, `services/delilegal/exceptions.py:5`, `agent/tool_loop.py:106`, `agent/tools/case_search.py:51`, `services/search.py:193`, `services/search.py:237`, `infrastructure/redis.py:337`, `services/rag/retriever.py:474` |
| 36 | FastMCP | 已实现 | `mcp.server.fastmcp.FastMCP` 注册 10 个工具（法条/类案/本地法条/综合检索 4 个 + 对比、起草、管辖、诉讼时效、合同审查、风险评估 6 个）；检索工具复用同一 Service Layer，`run_mcp.py` 支持 stdio/SSE 并先初始化 RAG。 | `mcp_server/server.py:6`, `mcp_server/tools/search.py:18`, `mcp_server/tools/search.py:50`, `mcp_server/tools/search.py:84`, `mcp_server/tools/search.py:107`, `mcp_server/tools/compare.py:8`, `mcp_server/startup.py:7`, `run_mcp.py` |
| 37 | pytest | 已实现 | 配置 pytest + pytest-asyncio，覆盖图拓扑、Agent、工具、检索、State、Verifier、缓存、持久化、迁移、脱敏、可观测性与 MCP。本次完整执行 **382 passed, 9 skipped**；9 个 skip 中 3 个在配置真实 DSN 后转为 passed，其余 6 个是 RAG 全局单例未初始化的自我保护跳过。 | `pytest.ini`, `tests/`, `tests/integration/test_postgres_checkpointer.py`, `tests/test_infrastructure_persistence.py` |

## 3. 分类汇总

### 已实现（34）

FastAPI、LangGraph StateGraph、Stateful Agent Workflow、Intent Router、Planner、Plan-and-Execute、Supervisor、Legal Consultation Agent、Statute Retrieval Agent、Case Analysis Agent、Multi-Agent Collaboration、ReAct Agent Loop、LangChain Tool Calling、Local RAG、Structured Legal Chunking、Embedding、Qdrant、BM25、RRF、Reranker、Result Verifier、Citation Verification、Answer Generator、LangGraph State、Multi-turn Conversation、Context Compaction、PostgreSQL、PostgreSQL Checkpointer、Redis、Agent Trace、Tool Retry、Exception Handling、FastMCP、pytest。

### 部分实现（3）

Query Rewrite（节点职责与命名不一致）、得理 Law API、得理 Case API（均缺 `DELILEGAL_BASE_URL`，无法真实联调）。

### 未实现（0）

没有发现只有文档声明、缺少实际实现的清单项。

## 4. 实际验证记录

全部命令在 `E:\codelab\学习agent\Legal` 下、用仓库自带虚拟环境 `./.venv/Scripts/python.exe` 执行。基础设施为 `docker compose up -d postgres redis qdrant`（postgres:17.10-alpine / redis:7.4.10-alpine / qdrant v1.19.0），三者 healthcheck 均为 healthy。

| 验证项 | 命令或方式 | 结果 |
|---|---|---|
| 完整测试套件 | `python -m pytest -q -p no:randomly -rs` | **382 passed, 9 skipped, 24.05s** |
| PostgreSQL 核心持久化 | `TEST_DATABASE_URL=postgresql+asyncpg://legal:legal@127.0.0.1:5432/legal_test` + `CHECKPOINT_INTEGRATION_DSN=postgresql://legal:legal@127.0.0.1:5432/legal`，跑 `tests/test_infrastructure_persistence.py tests/integration/test_postgres_checkpointer.py` | **3 passed, 1.51s**（含真实 `alembic upgrade head` / `downgrade base`、`plan.api_key` 与 `Authorization` 脱敏为 `[REDACTED]`、跨 checkpointer 实例同 `thread_id` 恢复） |
| PostgreSQL 库与表 | `psql -U legal -d legal -tAc "select tablename from pg_tables where schemaname='public'"` | `checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations` 四张 LangGraph 表存在；`legal` 与 `legal_test` 两库存在 |
| Redis 往返 | 本机探针（`services/cache/*` 真实调用，非 FakeRedis） | `ping True`、status `ok`；响应缓存命中且 key = `legal:cache:response:1:<sha256>`、TTL 3600、不含问题明文；限流 limit=2 得到 `[True, True, False, False]`，key = `legal:ratelimit:probe:30:<digest>`、TTL 30、不含 subject 明文、`retry_after=30`；幂等首次 `acquired`、二次 `duplicate` 且记录 `in_progress` → `completed`；会话热层只保留白名单字段（注入的 `question` 被丢弃）；keyspace 仅 4 个 `legal:*` key |
| Redis 降级 | 探针指向未监听端口 `6399` | `ping False`、`execute` 返回默认值、status `degraded`、限流 `allowed=True degraded=True`——fail-open 生效，不抛错 |
| Qdrant 往返 | 本机探针，走项目自己的 `QdrantVectorStore` | `health_check True`；`count` 0 → 2；重复写入仍为 2（内容哈希去重）；余弦排序正确（1.0 / 0.0）；`MatchValue` 与 `MatchAny` 过滤均生效；按 ID 删除 → 1；删除 collection → 0 |
| Qdrant 索引与向量参数 | 真实服务端 collection 信息 | 15 个 keyword payload 索引；`{'size': 512, 'distance': 'Cosine'}`；此前完整栈上 `legal_knowledge` 为 green、`points_count=8941`，另有 `legal_memory` |
| 应用健康检查 | 完整容器栈 `/api/health` | `{"status":"ok","provider":"deepseek","database":"ok","redis":"ok"}` |
| FastMCP 注册表 | 注册表探针 | 10 个工具，含 `search_law`、`search_case`、`search_local_law`、`legal_search` |
| 得理 API | mock transport 测试 | 通过；真实服务未联调（`DELILEGAL_BASE_URL` 未配置，`DELILEGAL_API_KEY` 已配置） |
| ORM 交付状态 | `git check-ignore -v infrastructure/models/base.py`；`git ls-files infrastructure/models/` | check-ignore 退出码 1（未被忽略）；`ls-files` 为空，目录状态 `??`——已可提交但尚未提交 |

关于 8,941 这个数字的口径：它来自本轮更早时候完整容器栈（含 app 服务）运行期的观测，与本地 70 个法律文件切分出 8,941 个 chunk 相互印证。当前这组 Qdrant 卷是后来新建的，`/collections` 为空，因为只跑了 postgres/redis/qdrant 三个基础服务，没有再启动负责建索引的 app 服务。

一个与代码无关但值得记录的环境问题：Windows 宿主机上即使没有任何代理环境变量，httpx 仍会通过 `urllib.request.getproxies()` 读取系统代理设置，导致直连 `127.0.0.1:6333` 返回空 body 的 502。本机验证需要 `NO_PROXY=127.0.0.1,localhost`（curl 用 `--noproxy '*'`）。容器内的 app 没有这个问题，这也解释了为什么完整栈里的 Qdrant 调用一直正常。

## 5. 与上一版快照的差异

上一版（PostgreSQL + Redis + Qdrant 容器化验收当时的快照）结论为「有条件不通过，31 已实现 / 6 部分实现」。本次修正如下：

| 条目 | 上一版 | 本次 | 依据 |
|---|---|---|---|
| PostgreSQL | 部分实现（仅 SQLite 替身） | 已实现 | 真实容器上 upgrade/断言/downgrade 全流程通过 |
| PostgreSQL Checkpointer | 部分实现（无 DSN 被跳过） | 已实现 | 集成测试通过 + 真实库中四张 checkpoint 表存在 |
| Redis | 部分实现（FakeRedis） | 已实现 | 真实容器往返 + 断网 fail-open 双向验证 |
| Qdrant | 已实现但「默认实际跑 Chroma」 | 已实现且为唯一后端 | `chroma_store.py` 已删除，`services/rag/__init__.py:40` 拒绝非 qdrant |
| `.gitignore` 屏蔽 ORM（P0） | 阻塞项 | 已消除，剩提交动作 | `.gitignore:24` 锚定 `/models/`，check-ignore 退出码 1 |
| `POSTGRES_REQUIRED` 降级启动 | 可降级 | 不存在该路径 | `main.py:76` ping 失败一律 `RuntimeError` |
| 测试规模 | 365 passed / 7 skipped | 382 passed / 9 skipped | 本次实测 |

## 6. 剩余差距与建议

1. **P1 · 明确 Query Rewrite 职责。** 两条路都可接受：让 `query_rewrite_node` 真正调用 `rewrite_query()` 并写回 `AgentState.rewritten_query`；或把节点更名为 `query_normalize`，同时把 RAG 内部的 rewrite/HyDE 状态通过 trace 事件暴露到顶层链路。当前状态下，读图的人会以为顶层已经做了语义改写。
2. **P1 · 得理接口真实联调。** 配置 `DELILEGAL_BASE_URL` 后逐项验证请求字段、分页、错误码、空结果与响应字段漂移，并确认重试不会造成重复计费。这一项在拿到 endpoint 前无法在本机推进。
3. **P2 · 清掉临时脚本并做一次干净检出验证。** 第 1 节清单已全部入库（含 `AGENTS.md`），
   只剩根目录的 `_diag_retrieval.py` 是临时排查脚本，未跟踪也不应跟踪，建议直接删除。
   随后在临时干净检出中执行 `import main`、`alembic upgrade head` 与完整测试，确认没有第二处交付缺口。
4. **P2 · 处理不可达的合同 Agent。** `agent/agents/contract_agent.py` 与 `services/contract_agent/` 有完整实现，但图里没有 `add_node`，`AssignedAgent` 字面量也不含它，`api/chat.py:424` 的流式节点白名单里那个 `"contract_agent"` 永远不会命中。要么接入图并扩展 `TaskType`/`TASK_AGENT_MAP`，要么删除，避免读者误判能力边界。
5. **P2 · 把真实后端验收固化进 CI。** 本次的 PostgreSQL / Redis / Qdrant 验证都是人工探针。建议改成带 integration marker 的测试，在 CI 里用 service container 跑，避免下一轮又退回替身验证。
6. **P2 · 修一处环境适配。** 若希望本机（非容器）联调 Qdrant 免踩系统代理，可在 `services/rag/qdrant_store.py` 构造 client 时对 loopback 地址显式禁用 env 代理，而不是依赖调用方设置 `NO_PROXY`。

