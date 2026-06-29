# 验证报告

> 最近更新：2026-06-29
> 验证环境：macOS，本地虚拟环境 `.venv`，Python 3.12，Node.js 20+，VitePress 1.6.4
> 验证范围：GitHub 展示材料、截图、文档构建、pytest 测试、密钥扫描、运行数据清理

## 总览

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| Python 测试 | 通过 | `176 passed, 6 skipped, 3 warnings` |
| 文档构建 | 通过 | `npm run build` 成功生成 VitePress 静态站点 |
| 页面截图 | 通过 | 对话页、后台看板、架构文档均为真实本地渲染截图 |
| 控制台错误 | 通过 | 截图采集时三张页面均未捕获相关 error/warn |
| 密钥扫描 | 通过 | 未发现真实 API key、GitHub token、AWS key 或私钥 |
| 运行数据清理 | 通过 | `data/chroma_db/` 与 SQLite checkpoint 从 Git 跟踪中移除 |
| GitHub Pages | 已配置 | workflow 构建 VitePress 并发布到 `gh-pages` 分支，目标地址为 `https://2249619829.github.io/Legal/` |
| GitHub CI | 已配置 | `.github/workflows/ci.yml` 运行 pytest smoke suite 和文档构建 |

## 测试结果

命令：

```bash
.venv/bin/python -m pytest -q
```

结果：

```text
176 passed, 6 skipped, 3 warnings
```

跳过项来自 `tests/test_rag.py` 中需要完整检索器初始化或慢速集成环境的用例。3 个 warning 是 `pytest.mark.slow` 未注册的提示，不影响测试通过状态。

## 文档构建

命令：

```bash
cd docs
npm run build
```

结果：VitePress 构建成功。构建时出现 Mermaid/代码高亮相关提示和 chunk size warning，属于前端构建警告，不阻断部署。

## 截图证据

### 对话页面

![法律咨询对话页](/screenshots/chat-desktop.png)

说明：截图来自本地 FastAPI 服务 `http://127.0.0.1:8000/`，页面显示历史会话、上下文状态、法律回答卡片和输入框。

### 后台看板

![后台看板](/screenshots/admin-dashboard.png)

说明：截图来自本地 FastAPI 服务 `http://127.0.0.1:8000/admin`，页面展示 trace 总数、成功率、平均耗时、LLM 调用、失败调用、fallback 和评测次数。

### 架构文档

![架构文档](/screenshots/architecture-docs.png)

说明：截图来自 VitePress 本地预览 `http://127.0.0.1:5173/Legal/guide/architecture`，页面展示系统架构图和文档导航。

## GitHub 展示项

| 项目 | 状态 |
| --- | --- |
| 根目录 README | 已新增，包含项目定位、截图、快速开始、测试、目录结构 |
| 在线文档 | 已配置 GitHub Pages workflow |
| 项目截图 | 已保存到 `docs/public/screenshots/` |
| 架构说明 | 已有 `PROJECT_INFO.md`、`docs/guide/architecture.md` |
| 测试说明 | README 和本报告均包含测试命令与结果 |
| CI | 已新增 `.github/workflows/ci.yml` |

## 清理说明

以下内容是本地运行产物，不适合进入 GitHub 仓库：

- `data/chroma_db/`
- `data/checkpoints.sqlite-shm`
- `data/checkpoints.sqlite-wal`
- `.env`
- `.venv/`
- `models/`
- `output/`
- `tmp/`
- `docs/.vitepress/dist/`
- `docs/.vitepress/cache/`

`.gitignore` 已覆盖这些路径；本轮同时把远端已跟踪的 ChromaDB 和 checkpoint 文件从 Git 索引中移除，保留本地文件不删除。

## 密钥检查

本轮使用正则扫描 Git 跟踪内容，覆盖：

- OpenAI/兼容 API key 形态：`sk-...`
- `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GITHUB_TOKEN`
- AWS access key：`AKIA...`
- PEM/OpenSSH 私钥头

扫描未发现真实密钥。仓库中仅保留 `.env.example` 的占位值，例如 `sk-xxx`、`tvly-xxx`。

## 剩余风险

- GitHub Pages workflow 会发布静态文件到 `gh-pages` 分支；若 `https://2249619829.github.io/Legal/` 仍返回 404，需要在 GitHub Settings -> Pages 中选择 `Deploy from a branch`，分支选 `gh-pages`，目录选 `/ (root)`。
- CI 使用 smoke suite，避免在 GitHub runner 上下载本地大模型；完整 RAG 集成仍建议在有模型和索引的本地环境运行。
- README 中的在线文档链接需要等 GitHub Actions 部署成功后才会返回页面。
