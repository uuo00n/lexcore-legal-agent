# 开发指南

## 前置条件

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 后端运行时 |
| Ollama | 0.24+ | 本地 HyDE 查询增强模型 |
| Node.js | 20+ | 文档站点开发（可选） |

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
| `LLM_PROVIDER` | LLM 提供商 | `zhipu` / `deepseek` / `qwen` / `ollama` |
| `ZHIPU_API_KEY` | 智谱 API Key（默认 provider 使用） | `sk-xxx` |

**可选项：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_MODEL` | 按 Provider 默认 | 模型名覆盖 |
| `LLM_BASE_URL_OVERRIDE` | — | API 地址覆盖 |
| `DEEPSEEK_API_KEY` | — | 切换到 `deepseek` 时填写 |
| `DASHSCOPE_API_KEY` | — | 切换到 `qwen` 时填写 |
| `EMBEDDING_MODEL` | `models/bge-small-zh-v1.5` | Embedding 模型路径 |
| `RERANKER_MODEL` | `models/bge-reranker-base` | Reranker 模型路径 |
| `HYDE_ENABLED` | `true` | 是否启用查询增强 |
| `HYDE_MODEL` | `qwen2.5:1.5b` | HyDE 用的 Ollama 模型 |
| `HYDE_LLM_BASE_URL` | `http://localhost:11434/v1` | HyDE 用 Ollama OpenAI 兼容地址 |
| `RETRIEVER_TOP_K` | `20` | 每路检索候选数量 |
| `RERANKER_SCORE_THRESHOLD` | `0.3` | Reranker 分数阈值 |
| `RRF_K` | `60` | RRF 融合常数 |
| `MAX_TOOL_CALLS` | `6` | ReAct 最大循环次数 |
| `MAX_UPLOAD_MB` | `10` | 上传文件大小限制 |
| `DELILEGAL_BASE_URL` | — | 得理 OpenAPI 地址，必须显式配置 |
| `DELILEGAL_APP_ID` / `DELILEGAL_SECRET` | — | 得理 OpenAPI 凭据，不得写入日志或提交仓库 |
| `DELILEGAL_LAW_SEARCH_PATH` | `/api/qa/v3/search/queryListLaw` | 法规检索路径 |
| `DELILEGAL_CASE_SEARCH_PATH` | `/api/qa/v3/search/queryListCase` | 类案检索路径 |

### 4. 安装 Ollama 并拉取模型

```bash
# macOS
brew install ollama
# 或从 https://ollama.com/download 下载

ollama pull qwen2.5:1.5b
```

### 5. 构建法条索引（首次）

```bash
python -m services.indexer.builder
```

索引构建完成后存储在 `data/chroma_db/`，后续启动会自动加载。如需重建：

```bash
python -m services.indexer.builder --rebuild
```

## 启动服务

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload \
  --reload-exclude 'data/*' \
  --reload-exclude 'data/**' \
  --reload-exclude 'models/*' \
  --reload-exclude 'models/**' \
  --reload-exclude '*.sqlite' \
  --reload-exclude '*.sqlite-*'
```

注意：不要让 `--reload` 监听 `data/`、`models/` 或 SQLite 文件。聊天、记忆、缓存和 checkpoint 会频繁写入这些文件；如果被热重载监听，长对话会在生成途中被重启打断，前端表现为一直停在“正在分析...”。

启动后访问 http://localhost:8000 即可使用。

## 运行测试

```bash
# 快速测试（跳过慢速集成测试）
pytest tests/ -m "not slow"

# 完整测试（包含 RAG 管线集成测试）
pytest tests/
```

## 代码规范

- 类型注解：所有函数签名使用 type hints
- 接口抽象：使用 Python Protocol 定义可替换接口
- 异步优先：API 层和 Agent 层全部使用 async/await
- 注释语言：中文注释，函数/类/模块级别完整文档
