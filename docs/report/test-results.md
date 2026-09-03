# 验证报告

> 最近更新：2026-09-03
> 验证环境：Windows 11 Pro，本地虚拟环境 `.venv`（Python 3.13.15），Node.js 24.18.0，VitePress 1.6.4
> 验证分支：`codex/refactor-legal-data-sources`
> 验证范围：pytest 全量测试、VitePress 文档构建、密钥扫描、环境变量口径、运行数据清理、CI 与 Pages 配置

## 总览

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| Python 测试 | 通过 | `382 passed, 9 skipped`，无 warning |
| 文档构建 | 通过 | `npm run build` 成功，无 dead link |
| 密钥扫描 | 通过 | 对 Git 跟踪内容做正则扫描，未命中任何真实凭证 |
| 环境变量口径 | 通过 | 源码读取的 102 个环境变量已全部出现在 `.env.example` |
| 运行数据清理 | 通过 | `.gitignore` 覆盖全部本地产物；仓库不跟踪向量库、模型权重与上传件 |
| GitHub Pages | 已配置 | `.github/workflows/docs.yml` 构建 VitePress 并强推 `gh-pages` 分支 |
| GitHub CI | 已配置 | `.github/workflows/ci.yml` 跑 9 个文件的 smoke suite + 文档构建两个 job |

## 测试结果

命令（仓库根目录）：

```bash
.venv/Scripts/python.exe -m pytest -q
```

结果：

```text
382 passed, 9 skipped
```

耗时随机器波动，本机三次实测分别为 17.04s、18.01s 和 24.05s。

测试规模：`tests/` 下 66 个单测模块 + `tests/integration/` 下 5 个集成模块。

### 9 个跳过项

全部是「缺少外部依赖时主动跳过」，不是失败：

| 数量 | 位置 | 跳过条件 |
| --- | --- | --- |
| 1 | `tests/integration/test_postgres_checkpointer.py:46` | 未设置 `CHECKPOINT_INTEGRATION_DSN` |
| 2 | `tests/test_infrastructure_persistence.py:60,81` | 未设置 `TEST_DATABASE_URL` |
| 6 | `tests/test_rag.py:390,411,434,465,487,511` | 检索器未初始化（需要 embedding 权重与 Qdrant 索引） |

配好对应环境变量后这些用例会真实执行；默认跳过是为了让 `pytest -q` 在没有数据库和索引的机器上
也能干净跑完。

### Redis 测试隔离

`tests/conftest.py` 有一个 autouse fixture `_isolate_redis`，把 `REDIS_ENABLED` 强制置为
`false` 并在用例前后调用 `reset_for_tests()`。原因是 `main.py` 在导入时执行 `load_dotenv()`，
会把开发机 `.env` 里的 `REDIS_URL` 注入 `os.environ`；本机按项目文档跑过
`docker compose up -d postgres redis qdrant` 之后，缓存层就会连上真实 Redis，检索用例会读到上
一次运行留下的缓存，导致 `HybridRetriever` 根本不执行、拿不到调用记录与 trace 事件，测试结果
随本机 Redis 内容漂移。需要 Redis 行为的用例（`tests/test_redis_cache.py`）自行注入替身客户端，
不受该 fixture 影响。

CI 只跑 9 个文件的 smoke suite 且不起 Redis，所以这个问题在 CI 上不会暴露，只在本地全量跑时出现。

## 文档构建

命令：

```bash
cd docs
npm run build
```

结果：

```text
build complete in 8.33s
```

耗时同样随机器波动，本机实测区间为 8.05s ~ 8.33s。

VitePress 的 `ignoreDeadLinks` 现在只保留 `/localhost/` 一条——原先用来屏蔽
`docs/finetune-qwen-law-sft.md` 里 `/Users/didi/Desktop/Legal/scripts/...` 绝对路径链接的规则
已随那些路径一起删除，因此文档里的相对链接现在是真检查的。构建剩余输出只有一条 chunk size
> 500 kB 的前端体积提示，不阻断部署。

## 截图证据

::: tip 这三张是 2026-06-29 采集的历史截图
本轮验证只跑了 pytest 与文档构建，没有重新起服务采集页面，所以截图沿用上一轮的产物。UI 结构
此后没有改动，但看板里的 trace 数量、成功率等数字是当时那一刻的运行数据，不代表当前状态。
:::

### 对话页面

![法律咨询对话页](/screenshots/chat-desktop.png)

说明：截图来自本地 FastAPI 服务 `http://127.0.0.1:8000/`，页面显示历史会话、上下文状态、法律回答卡片和输入框。

### 后台看板

![后台看板](/screenshots/admin-dashboard.png)

说明：截图来自本地 FastAPI 服务 `http://127.0.0.1:8000/admin`，页面展示 trace 总数、成功率、平均耗时、LLM 调用、失败调用、fallback 和评测次数。

### 架构文档

![架构文档](/screenshots/architecture-docs.png)

说明：截图来自 VitePress 本地预览 `http://127.0.0.1:5173/Legal/guide/architecture`，页面展示系统架构图和文档导航。

## CI 与 Pages 配置

`.github/workflows/ci.yml`（push / PR 到 `main`，或手动触发）有两个并行 job：

- `test`：Ubuntu + Python 3.12，安装 `requirements.txt` 与 `requirements-dev.txt`，跑 9 个文件的
  smoke suite —— `test_auth`、`test_cache`、`test_context_compaction`、`test_contract_agent_core`、
  `test_gateway`、`test_model_routing`、`test_quota`、`test_task_queue`、`test_static_app_js`。
- `docs`：Node.js 24 + `npm ci` + `npm run build`。

smoke suite 刻意不覆盖 RAG 与持久化：GitHub runner 上没有 embedding/reranker 权重、没有
PostgreSQL、也没有 Qdrant 索引，全量跑只会大面积跳过或超时。代价是本地才会暴露的问题（例如上面
那个 Redis 隔离缺陷）CI 拦不住，合并前仍需在有依赖的环境跑一遍 `pytest -q`。

`.github/workflows/docs.yml` 只在 `docs/**` 变更时触发，构建产物加 `.nojekyll` 后强推到
`gh-pages` 分支，用 `concurrency: pages` 串行化，避免两次部署互相覆盖。

## 运行数据清理

本地运行产物一律不进仓库，`.gitignore` 当前覆盖：

| 条目 | 内容 |
| --- | --- |
| `.env` | 真实密钥 |
| `.venv/`、`venv/`、`__pycache__/`、`*.pyc` | Python 环境与字节码 |
| `/models/` | 根目录模型权重（`infrastructure/models/` 是 ORM 源码，不受影响） |
| `*.safetensors`、`*.bin`、`*.pt`、`*.pth` | 任意位置的权重文件 |
| `data/*.db` | 遗留的本地 SQLite 文件 |
| `data/uploads/*`、`data/evidence/*`、`data/viking_context/*` | 用户上传与运行产物，各保留 `.gitkeep` |
| `data/reports/`、`output/`、`tmp/`、`.runtime/` | 生成的报告与临时目录 |
| `data/finetune/`、`data/DISC-Law-SFT-*.jsonl` | 微调语料与派生数据集 |
| `docs/.vitepress/dist/`、`docs/.vitepress/cache/`、`docs/node_modules/` | 文档构建产物与依赖 |
| `.pytest_cache/`、`.coverage`、`htmlcov/` | 测试缓存与覆盖率报告 |

向量库不再需要单独忽略：Qdrant 数据存在 Docker 命名卷里，PostgreSQL 承担全部关系型持久化与
checkpoint，仓库里不存在 `data/chroma_db/` 或 `data/checkpoints.sqlite*` 这类落盘目录。
`data/laws/` 与 `data/templates/` 是随代码版本管理的只读资产，正常跟踪。

## 密钥检查

对 `git ls-files` 列出的全部跟踪内容做正则扫描，覆盖：

- OpenAI/兼容 API key：`sk-` + 20 位以上字符
- AWS access key：`AKIA` + 16 位大写字母数字
- GitHub token：`ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` + 20 位以上字符
- PEM / OpenSSH 私钥头

扫描无命中。仓库中只保留 `.env.example` 的占位值（如 `sk-xxx`、`sk-your-api-key`），长度短于阈值，
也不是真实凭证。日志侧另有 `infrastructure/sanitize.py` 的 `RedactingFormatter` 兜底，
`main.py` 启动时已把它装到根 handler 上。

## 环境变量口径检查

用正则扫描全部非测试 Python 源码里的 `os.getenv` / `os.environ`，得到 102 个运行时读取的变量，
再与 `.env.example` 中声明的键名求差集，结果为空——源码读得到的变量全部在示例配置里有对应条目。
本轮补齐的是此前只存在于代码中的一批：`MAX_STEP_RETRIES`、`RERANKER_TOP_N`、`RETRIEVER_TOP_K`、
`QDRANT_BATCH_SIZE`、`QDRANT_FILTER_BATCH_SIZE`、`LAWS_DIR`、`EVIDENCE_DIR`、
`MAX_VIDEO_UPLOAD_MB`、`VIKING_CONTEXT_ROOT`、`MCP_TRANSPORT` / `MCP_SSE_HOST` / `MCP_SSE_PORT`、
`FACT_AGENT_*` 旧别名、`TEST_DATABASE_URL` / `CHECKPOINT_INTEGRATION_DSN`，以及
`scripts/` 下 OpenViking 辅助脚本读取的十余项。

两点需要注意，都写进了 `.env.example` 的注释：

- `scripts/openviking_embedding_server.py:14` 的 `LEGAL_EMBEDDING_MODEL` 内置默认值仍是旧机器的
  绝对路径 `/Users/didi/Desktop/Legal/models/bge-small-zh-v1.5`，非 macOS 环境必须显式设置该变量。
  `scripts/start_openviking_glm47.py:141` 的 `OPENVIKING_SERVER_BIN` 同理，默认是 `/tmp/...`。
- `run_mcp.py:40` 的 `MCP_SSE_HOST` 内置默认值是 `0.0.0.0`，而 FastMCP 自身不做鉴权。
  该变量只在 `MCP_TRANSPORT=sse` 时生效，示例配置里给的推荐值是 `127.0.0.1`。

## 剩余风险

- CI 的 smoke suite 只有 9 个文件，RAG、持久化、Redis 相关缺陷只能靠本地全量 `pytest -q` 拦住。
- 6 个 RAG 用例与 3 个数据库用例默认跳过；要真正验证这两条链路，需要准备 embedding 权重 + Qdrant
  索引，并设置 `TEST_DATABASE_URL` 与 `CHECKPOINT_INTEGRATION_DSN`。
- 截图是 2026-06-29 的历史产物，看板数字不反映当前运行状态。
- GitHub Pages 若返回 404，需在 Settings → Pages 选 `Deploy from a branch`，分支 `gh-pages`，
  目录 `/ (root)`；`docs.yml` 只负责推分支，不负责开启 Pages。
- 本报告的两项结论（`382 passed, 9 skipped`、文档构建成功且无 dead link）绑定
  `codex/refactor-legal-data-sources` 分支当前的工作区状态，代码变更后需要重跑。
- 上一轮报告提到的「未跟踪文件导致干净检出跑不起来」已闭合：`infrastructure/models/`、
  `infrastructure/operational_store.py`、`0002_operational_storage.py`、三个防回归测试、Docker 编排、
  `docs/refactor/` 文档与 `AGENTS.md` 均已入库。工作区只剩根目录临时脚本 `_diag_retrieval.py` 待清理，
  它不参与运行也不应入库。详见 [最终差距分析](../refactor/final-gap-analysis.md)。
