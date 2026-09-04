# 开发指南

## 前置条件

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 后端运行时 |
| PostgreSQL | 17+ | 业务、可观测与 checkpoint 持久化，**硬依赖** |
| Qdrant | 1.19+ | 法条与长期记忆向量库，**硬依赖** |
| Redis | 7.4+ | 缓存、限流、会话热层、幂等（可缺省，缺省即降级） |
| Node.js | 20+ | 文档站点开发（可选） |
| Ollama | 0.24+ | 仅在把 `LLM_PROVIDER` 或 HyDE 切回本地模型时需要（可选） |

最省事的做法是用 Compose 只拉起三个数据服务，应用仍在本机跑：

```bash
docker compose up -d postgres redis qdrant
```

## 环境搭建

### 1. 克隆项目

```bash
git clone <repo-url>
cd Legal
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如需运行测试或 RAGAS 评测，再安装开发依赖：

```bash
pip install -r requirements-dev.txt
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

**必填项：**

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_PROVIDER` | LLM 提供商，默认 `deepseek` | `deepseek` / `zhipu` / `qwen` / `ollama` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（默认 provider 与 HyDE 共用） | `sk-xxx` |
| `DATABASE_URL` | PostgreSQL DSN，连不上时应用拒绝启动 | `postgresql+asyncpg://legal:change-me@localhost:5432/legal` |
| `QDRANT_URL` | Qdrant 地址 | `http://localhost:6333` |

**可选项：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_MODEL` | 按 Provider 默认（DeepSeek 为 `deepseek-v4-pro`） | 模型名覆盖 |
| `LLM_BASE_URL_OVERRIDE` | — | API 地址覆盖 |
| `LLM_FALLBACK_PROVIDERS` | — | 主 provider 失败后按序尝试的备用 provider |
| `ZHIPU_API_KEY` | — | 切换到 `zhipu` 时填写 |
| `DASHSCOPE_API_KEY` | — | 切换到 `qwen` 时填写 |
| `EMBEDDING_MODEL` | `models/bge-small-zh-v1.5` | Embedding 模型路径 |
| `RERANKER_MODEL` | `models/bge-reranker-base` | Reranker 模型路径 |
| `HYDE_ENABLED` | `true` | 是否启用查询增强 |
| `HYDE_BACKEND` | `openai` | `openai`（OpenAI 兼容接口）或 `hf_lora`（本地 Qwen + LoRA） |
| `HYDE_MODEL` | `deepseek-v4-flash` | HyDE 与问题重写使用的轻量模型 |
| `HYDE_LLM_BASE_URL` | `https://api.deepseek.com` | HyDE 的 OpenAI 兼容地址；默认复用 `DEEPSEEK_API_KEY` |
| `RETRIEVAL_VECTOR_TOP_K` | `10` | 向量召回候选数量 |
| `RETRIEVAL_BM25_TOP_K` | `10` | BM25 召回候选数量 |
| `RETRIEVAL_FINAL_TOP_K` | `5` | Reranker 最终返回数量 |
| `RERANKER_SCORE_THRESHOLD` | `0.3` | Reranker 分数阈值 |
| `RETRIEVAL_MIN_RESULTS` | `1` | 全部低于阈值时至少保留的条数 |
| `RETRIEVAL_INCLUDE_SUPERSEDED` | `false` | 已废止 / 历史版本条文是否参与召回 |
| `RRF_K` | `60` | RRF 融合常数 |
| `MAX_TOOL_CALLS_PER_AGENT` | `2` | 每个 Agent 任务的最大工具调用次数（旧名 `MAX_TOOL_CALLS` 仍作兜底） |
| `MAX_TOOL_CALLS_PER_REQUEST` | `3` | 一次请求内所有 Agent / 计划步骤 / 修复轮累计的工具调用上限 |
| `EVIDENCE_LAW_TARGET` | `5` | 法条证据达到该条数即停止继续检索 |
| `EVIDENCE_CASE_TARGET` | `3` | 案例证据达到该条数即停止继续检索 |
| `EVIDENCE_GAIN_STOP_THRESHOLD` | `0` | 上一轮新增证据 ≤ 该值即停止工具循环 |
| `MAX_UPLOAD_MB` | `10` | 上传文件大小限制 |
| `CHECKPOINT_BACKEND` | `postgres` | 无数据库的纯单测环境可设为 `memory` |
| `REDIS_URL` | — | 留空即缓存、限流、幂等全部降级运行 |
| `ADMIN_API_KEY` | — | 配置后 `/api/admin/*` 需要 `X-Admin-Key` |
| `DELILEGAL_BASE_URL` | — | 得理开放平台地址，当前为 `https://platform.delilegal.com`，必须显式配置 |
| `DELILEGAL_API_KEY` | — | 得理 Bearer API Key，不得写入日志或提交仓库 |
| `DELILEGAL_LAW_SEARCH_PATH` | `/api/v1/generice/law/list` | 法规检索路径 |
| `DELILEGAL_CASE_SEARCH_PATH` | `/api/v1/generice/case/list` | 类案检索路径 |

完整清单见 [`.env.example`](https://github.com/2249619829/Legal/blob/main/.env.example)。

### 4. 执行数据库迁移

```bash
alembic upgrade head
```

DDL 全部由 Alembic 负责，应用启动只校验表是否齐全。跳过这一步会直接报
`PostgreSQL schema is incomplete; run 'alembic upgrade head'`。

### 5. 构建法条索引（首次）

```bash
python -m services.indexer.builder
```

索引构建完成后存储在 Qdrant `legal_knowledge` collection，后续启动会自动加载。如需重建：

```bash
python -m services.indexer.builder --rebuild
```

## 启动服务

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload \
  --loop services.checkpoint:selector_event_loop_factory \
  --reload-exclude 'data/*' \
  --reload-exclude 'data/**' \
  --reload-exclude 'models/*' \
  --reload-exclude 'models/**'
```

`--loop services.checkpoint:selector_event_loop_factory` 用于确保 Windows 下的
psycopg 异步连接运行在 `SelectorEventLoop`；Linux/macOS 也可使用同一命令。

注意：不要让 `--reload` 监听 `data/` 或 `models/`。上传与本地上下文运行文件可能频繁变化；如果被热重载监听，长对话会在生成途中被重启打断，前端表现为一直停在“正在分析...”。

启动后访问 http://localhost:8000 即可使用。

## 独立启动 MCP Server（可选）

FastMCP 是与 Web 链路平行的对外暴露层，**不是 FastAPI 的子进程**：`main.py` 的 lifespan
从不拉起 MCP 进程，Agent 工具直接调用进程内 Service Layer。需要把法律工具暴露给
Claude Desktop 之类的 MCP 客户端时，单独启动：

```bash
python run_mcp.py                      # 默认 stdio
MCP_TRANSPORT=sse python run_mcp.py    # 改用 SSE
```

它启动时会自行调用 `initialize_rag()`，与 FastAPI 复用同一套索引与检索器实现。

## 运行测试

```bash
# 全量（默认不需要真实外部服务，依赖缺失的用例自动 skip）
pytest -q

# 跳过较慢的本地索引用例
pytest -m "not slow"

# 只跑需要真实外部服务的集成用例
pytest -m integration
```

集成用例通过环境变量选择真实后端：`TEST_DATABASE_URL` 指向可写的 PostgreSQL 测试库，
`CHECKPOINT_INTEGRATION_DSN` 指向 checkpointer 用的 psycopg DSN；未设置时相关用例 skip。

## 代码规范

- 类型注解：所有函数签名使用 type hints
- 接口抽象：使用 Python Protocol 定义可替换接口
- 异步优先：API 层和 Agent 层全部使用 async/await
- 注释语言：中文注释，函数/类/模块级别完整文档
