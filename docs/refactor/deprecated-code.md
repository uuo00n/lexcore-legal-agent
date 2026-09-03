# 第二十五阶段：废弃代码审计与删除清单

::: warning 这是第二十五阶段的决策记录，其中 5 条"否"已被后续阶段推翻
下面标注了 ⚠️ 的行（Chroma 向量库、Chroma 长期记忆、SQLite 文档元数据与 SQLite cache/quota/
observability）在本阶段的结论是"保留"，但第二十六阶段完成 PostgreSQL + Redis + Qdrant 迁移后
已经全部删除。清单按原样保留决策过程，各行的 ⚠️ 注释说明后来的实际结果。
:::

## 审计前提

- 审计分支：`codex/refactor-legal-data-sources`
- 审计基线：`19477d7`（“整理最终 Graph 拓扑”）
- 删除前完整测试：`343 passed, 7 skipped, 3 warnings`
- 判断标准：主链无引用、测试仅验证兼容入口、且已有稳定替代实现时才删除；运行中仍有调用或仍承担降级职责的代码保留。
- 历史设计文档中的旧名称属于阶段记录，不作为运行时引用，也不随源码删除。

## 决策清单

| 文件或符号 | 是否删除 | 原因 | 替代实现 |
| --- | --- | --- | --- |
| `agent/agents/fact_agent.py` | 是（迁移后删除） | 文件内主体已经是 Case Analysis Agent；`fact_agent_node`、`fact_check_node` 仅为旧名称兼容，最终 Graph 不再引用 | `agent/agents/case_analysis_agent.py::case_analysis_agent_node` |
| `tests/test_fact_check_node.py` | 是（测试迁移） | 只验证旧 `fact_check_node` 与旧条件边 | Case Analysis 行为由 `tests/test_specialist_agents.py`、`tests/test_supervisor_nodes.py` 覆盖 |
| `agent/agents/__init__.py` 中 `agent_node`、`fact_agent_node`、`fact_check_node` | 部分删除 | 都是旧节点名称的转发导出，主图没有调用 | `legal_consult_agent_node`、`case_analysis_agent_node` |
| `agent/nodes/__init__.py` 中旧节点转发导出 | 部分删除 | `agent_node`、`fact_agent_node`、`fact_check_node`、`verifier_node` 和旧拓扑条件边只剩兼容测试引用 | 显式的 Specialist、`result_verifier_node`、`should_execute_next`、`should_after_verifier` |
| `agent/nodes/routing.py` 中 `should_after_supervisor`、`should_enter_planner`、`should_after_planner`、`should_after_fact_check` | 部分删除 | 最终 Graph 已固定为 Router → Planner → Supervisor → Specialist → Verifier → Answer Generator，不再注册这些旧条件边 | `should_execute_next`、`should_after_verifier` |
| `agent/agents/legal_consult_agent.py::agent_node` | 是 | 旧通用 Agent 名称的纯转发别名 | `legal_consult_agent_node` |
| `agent/nodes/verifier.py::verifier_node` | 是 | 结果核验与答案生成拆分后遗留的旧节点别名 | `result_verifier_node` 与 `answer_generator_node` |
| `agent/agents/contract_agent.py` | 否 | 虽未接入当前默认 Graph，但仍有结构化合同报告、报告持久化和独立测试；属于暂时未调度，不是废弃 | 保留现有 `contract_agent_node`，核心实现为 `services/contract_agent/` 与 `services/contract_report.py` |
| `services/contract_agent/` | 否 | 合同报告 API 和合同审查测试仍直接使用 | 无需替代 |
| `agent/tools/compare.py`、`risk.py`、`review.py`、`limitations.py`、`jurisdiction.py`、`draft.py` | 是 | 这些 Agent 侧包装器未绑定到任何当前 Specialist ToolNode；业务能力已经下沉到 Service Layer，并由 FastMCP 作为扩展服务暴露 | `services/legal_tools.py`、`services/jurisdiction.py`、`mcp_server/tools/` |
| `agent/tools/__init__.py::ALL_TOOLS` | 是 | 最终 Graph 按 Specialist 分别注册工具，`ALL_TOOLS` 仅被旧测试读取 | `CASE_ANALYSIS_TOOLS`、`STATUTE_RETRIEVAL_TOOLS`、`LEGAL_CONSULT_TOOLS` |
| `agent/tools/search.py` | 是 | 仅重新导出本地 RAG Tool 的旧模块路径 | `agent/tools/rag_search.py` |
| `services/mcp_client.py` | 是 | FastAPI 主链已改为进程内 Service Layer，应用生命周期不再启动 stdio MCP Client | Agent Tool → `services/search.py` / `services/local_legal_retriever.py`；FastMCP 仅由 `run_mcp.py` 独立暴露 |
| `tests/test_mcp_client.py` | 是 | 只验证已经脱离主链的 stdio MCP Client | `tests/test_fastmcp_service_layer.py` 与 Agent retrieval tool 测试 |
| `eval/run_eval.py` 中 stdio MCP Client 生命周期 | 部分删除 | e2e 评测仍是旧客户端最后一个真实调用方 | 与 FastAPI 一致调用 `services.rag.startup.initialize_rag()` |
| `mcp_server/knowledge/limitations_table.py` | 是 | 仅为旧导入路径转发，仓库内无调用 | `services/limitations_rules.py` |
| `services/vectorstore/milvus_store.py` | 是 | 构造即抛 `NotImplementedError`，且当前目标后端已经实现为 Qdrant | `services/rag/qdrant_store.py` |
| `services/vectorstore/__init__.py`、`base.py`、`chroma_store.py` | 是 | 都是迁移到 `services.rag` 后的导入兼容层，生产代码无引用 | `services/rag/__init__.py`、`interfaces.py`、`chroma_store.py`（⚠️ 后续阶段连 `services/rag/chroma_store.py` 一起删除，现只剩 `qdrant_store.py`） |
| `services/retriever/__init__.py`、`base.py`、`hybrid.py`、`keyword.py`、`reranker.py`、`semantic.py` | 是 | 都是 RAG 重构后的转发层，生产代码只保留同目录下仍在使用的 `hyde.py` | `services/rag/retriever.py`、`bm25.py`、`reranker.py`、`interfaces.py` |
| `services/rag` 中 `get_vectorstore`、`reset_store`、`LawVectorStore`、`KeywordRetriever`、`ChromaLawStore`、`QdrantLawStore` 等旧别名 | 是 | 仓库内除兼容测试外无调用，规范 API 已稳定 | `get_vector_store`、`reset_vector_store`、`VectorStore`、`BM25Retriever`、`ChromaVectorStore`、`QdrantVectorStore`（⚠️ `ChromaVectorStore` 后续也已删除） |
| `VECTORSTORE_TYPE` 旧环境变量 | 是 | 当前示例与部署配置统一使用 `VECTOR_STORE`，仓库内没有现行配置引用 | `VECTOR_STORE` |
| ⚠️ `services/rag/chroma_store.py` | 否 | `VECTOR_STORE=chroma` 仍是默认本地后端，也是 Qdrant 不可用时明确配置的本地方案 | **后续已推翻**：文件已删除，Qdrant 成为唯一向量后端，`requirements.txt` 也不再含 `chromadb`；`tests/test_storage_architecture.py::test_runtime_has_no_embedded_database_or_removed_vector_backend` 断言 `chroma_store` 不得重新出现在运行时代码里 |
| ⚠️ `services/memory_store.py` 的 Chroma 长期记忆 | 否 | 长期记忆仍直接使用独立 Chroma collection，尚未迁移到 Qdrant | **后续已推翻**：长期记忆已迁到 Qdrant `legal_memory` collection（`QDRANT_MEMORY_COLLECTION`），由 `main.py` lifespan 的 `init_memory_store()` 初始化 |
| ⚠️ `services/vectorstore/chroma.sqlite3` | 否 | 未跟踪的用户运行产物，不属于源码清理范围 | **已不再产生**：不存在 Chroma 后端，向量数据全在 Qdrant 命名卷里 |
| ⚠️ `services/checkpoint.py` 的 SQLite 上传文档元数据 | 否 | `docs` 表仍由上传与文档注入流程使用 | **后续已推翻**：文档元数据迁到 PostgreSQL，DDL 由 Alembic 管理。`services/checkpoint.py` 主体变为 `AsyncPostgresSaver` 生命周期，`save_doc` / `load_doc`（`services/checkpoint.py:206,232`）保留为薄封装，转调 `infrastructure/operational_store.py`，调用方 `api/upload.py` 无需改动 |
| ⚠️ SQLite cache、quota、observability、memory 相关实现 | 否 | 多个模块和 API 测试仍依赖 `DOCS_DB`；PostgreSQL 只替代了核心业务持久化和可选 checkpoint | **后续已推翻**：全部迁完，`DOCS_DB` / `CHROMA_DB_PATH` 环境变量已删除，`requirements.txt` 也不再含 `aiosqlite`；缓存改由 Redis 承担 |
| `agent/prompts.py::SUPERVISOR_FINAL_PROMPT` | 是 | 最终回答已由独立 Answer Generator 生成，该 Prompt 只剩旧测试引用 | `ANSWER_GENERATOR_PROMPT` |
| `agent/prompts.py::VERIFIER_FINAL_PROMPT` | 是 | 只是 `ANSWER_GENERATOR_PROMPT` 的旧导入别名 | `ANSWER_GENERATOR_PROMPT` |
| 其他 Planner、Specialist、Verifier、Answer Generator Prompt | 否 | 均有当前节点直接 import | 对应当前节点实现 |

## 删除顺序

1. 迁移 Case Analysis 文件名并清理旧 Agent/Node 名称。
2. 删除未注册的 Agent Tool 包装器与废弃 stdio MCP Client。
3. 删除旧 vectorstore/retriever 导入层，并把测试改到规范 `services.rag` API。
4. 删除旧 Prompt 和仅验证旧入口的断言。
5. 运行引用扫描、完整 pytest 和文档构建，确认没有悬空 import 或行为回归。

## 完成验证

- 废弃源码引用扫描：通过；剩余命中仅位于历史设计文档、清单本身和“禁止重新引入”的回归断言。
- Python 编译：`python -m compileall -q agent api eval infrastructure mcp_server services` 通过。
- 完整测试：`333 passed, 7 skipped, 3 warnings`。
- 文档构建：`cd docs && npm run build` 通过。
- 7 个 skip 与 3 个 warning 均为删除前已有项：PostgreSQL 集成 DSN 未配置、RAG 慢测未初始化，以及 `slow` marker 未注册。
