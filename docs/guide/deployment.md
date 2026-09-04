# 部署文档

## 生产环境要求

| 资源 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 5 GB（源码部署） | 10 GB（Docker，含镜像、构建缓存与数据卷） |
| Python | 3.12+ | 3.12+ |

::: tip 内存说明
bge-small-zh-v1.5 和 bge-reranker-base 各占约 400MB 内存。HyDE 默认走云端
`deepseek-v4-flash`，不额外占本机内存；若把 `LLM_PROVIDER` 或 `HYDE_BACKEND` 切回本地模型
（Ollama qwen2.5:1.5b 或 `hf_lora`），需要额外预留 1GB 以上。
:::

::: tip CPU 算力说明
embedding 与 reranker 全部跑在 CPU 上，不需要 GPU。实测（bge 本地权重、默认 20 个候选）：

| 环节 | 2 线程 | 4 线程 | 8 线程 |
|------|--------|--------|--------|
| reranker 精排 20 个候选 | 2.7 s | 1.9 s | 1.1 s |
| query embedding | 11 ms | 10 ms | 12 ms |

精排是唯一吃算力的环节，且已由 `asyncio.to_thread` 挪出事件循环，不会阻塞并发请求，
但多个请求会争抢 CPU。核数偏少时把 `RETRIEVAL_VECTOR_TOP_K` 与 `RETRIEVAL_BM25_TOP_K`
从 10 降到 5，候选数减半，精排耗时也大致减半。重复问题会命中 Redis 检索缓存，直接跳过精排。
:::

## 方式一：Docker Compose（推荐）

Compose 拉起 5 个服务：`postgres`、`redis`、`qdrant`、一次性的 `migrate`、以及 `app`。
`app` 只在三个数据服务健康、且 `migrate` 成功退出之后才启动，因此不存在「表还没建好
就接流量」的窗口。

### 1. 准备配置

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
```

`.env` 至少要填一个可用的 LLM API Key。文件里面向宿主机的 `DATABASE_URL`、
`REDIS_URL`、`QDRANT_URL` 不需要改：Compose 会用容器网络地址覆盖它们。`.env` 不存在时
`docker compose up` 会直接报错，这是有意的——没有 Key 的容器能启动但第一次问答必然失败。

### 2. 启动

```bash
docker compose up --build -d
docker compose ps
```

镜像装 CPU 版 PyTorch，`app` 不请求 GPU，主机也不需要 NVIDIA 驱动或 Container Toolkit。
`torch` 单独占一层（数百 MB），之后改 `requirements.txt` 不会触发重新下载。首次启动若
Qdrant 里没有索引，应用会现场生成 embedding 写入 `legal_knowledge`：8941 个法条分块在
8 线程上约 80 秒，核数少的机器要几分钟。

::: tip 首次构建太慢时走代理
`download.pytorch.org` 直连速率很不稳定，实测 BuildKit 只能跑到 0.3 MB/s，而 torch 的
CPU wheel 也有数百 MB。本机有 HTTP 代理时，用 Docker 的预定义 build arg 转发进去即可
（把 7897 换成自己的端口；Linux 宿主机上 `host.docker.internal` 需要额外
`--add-host=host.docker.internal:host-gateway`，或直接写宿主机 IP）：

```bash
docker compose build app \
  --build-arg HTTP_PROXY=http://host.docker.internal:7897 \
  --build-arg HTTPS_PROXY=http://host.docker.internal:7897 \
  --build-arg NO_PROXY=localhost,127.0.0.1,postgres,redis,qdrant
```

`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 是 Docker 预置构建参数，不计入构建缓存
key，也不会写进最终镜像的环境变量，所以不用改 `Dockerfile`，产出的镜像与直连构建一致。
:::

### 3. 验证

```bash
curl http://localhost:8000/api/health
docker compose logs -f app
```

`migrate` 是一次性任务，`docker compose ps` 里显示为 `Exited (0)` 属于正常状态。

### 4. 数据库迁移

DDL 全部由 Alembic 负责，应用启动时只校验表是否齐全，不会执行 `CREATE TABLE`。
升级版本后重新执行迁移：

```bash
docker compose run --rm migrate
```

::: warning 清空数据
`docker compose down -v` 会同时删除应用运行数据、PostgreSQL、Redis、Qdrant 和模型
缓存卷，不可恢复。只想停服务用 `docker compose down`。
:::

## 方式二：源码部署

### 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

完整清单见仓库根目录的 `.env.example`，下面只列必填与最常改的项。源码部署需要
自行准备并启动 PostgreSQL、Redis、Qdrant——PostgreSQL 是硬依赖，连不上时应用拒绝启动。

```bash
# .env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
LLM_MODEL=deepseek-v4-pro

EMBEDDING_MODEL=models/bge-small-zh-v1.5
RERANKER_MODEL=models/bge-reranker-base
MODEL_DEVICE=cpu
DATABASE_URL=postgresql+asyncpg://legal:change-me@localhost:5432/legal
CHECKPOINT_BACKEND=postgres
VECTOR_STORE=qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=legal_knowledge
QDRANT_MEMORY_COLLECTION=legal_memory

# HyDE 默认复用 DeepSeek 的轻量模型；也可改回本地 Ollama
HYDE_ENABLED=true
HYDE_MODEL=deepseek-v4-flash
HYDE_LLM_BASE_URL=https://api.deepseek.com

RETRIEVAL_VECTOR_TOP_K=10
RETRIEVAL_BM25_TOP_K=10
RETRIEVAL_FINAL_TOP_K=5
RERANKER_SCORE_THRESHOLD=0.3
RRF_K=60
MAX_TOOL_CALLS_PER_AGENT=2
MAX_TOOL_CALLS_PER_REQUEST=3
EVIDENCE_LAW_TARGET=5
EVIDENCE_CASE_TARGET=3
EVIDENCE_GAIN_STOP_THRESHOLD=0

# 得理法律开放平台
DELILEGAL_BASE_URL=https://platform.delilegal.com
DELILEGAL_API_KEY=sk-your-api-key
DELILEGAL_LAW_SEARCH_PATH=/api/v1/generice/law/list
DELILEGAL_CASE_SEARCH_PATH=/api/v1/generice/case/list

UPLOAD_DIR=data/uploads
MAX_UPLOAD_MB=10

# Redis：检索缓存、得理响应缓存、突发限流、会话元数据、幂等标记
# 留空即全部降级运行，不影响接口可用性
REDIS_URL=redis://localhost:6379/0
RETRIEVAL_CACHE_TTL_SECONDS=1800
DELILEGAL_CACHE_TTL_SECONDS=3600
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
SESSION_METADATA_TTL_SECONDS=86400
IDEMPOTENCY_TTL_SECONDS=600
```

### 3. 初始化数据库

```bash
alembic upgrade head
```

应用启动时只校验表结构，不会建表；跳过这一步会直接报
`PostgreSQL schema is incomplete; run 'alembic upgrade head'`。

### 4. 构建索引

```bash
python -m services.indexer.builder
```

写入 Qdrant 的 `legal_knowledge`，需要 Qdrant 已在运行。索引已存在时自动跳过，
`--rebuild` 强制重建。

### 5. 启动服务

```bash
# 生产模式（多 worker）
gunicorn main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 \
  --bind 0.0.0.0:8000 \
  --timeout 120
```

::: warning 注意
worker 数建议不超过 CPU 核数：每个 worker 都会独立加载 embedding 与 reranker 权重
（各约 400MB），精排也共享同一份 CPU 算力。
:::

::: tip MCP Server 与 Web 服务相互独立
FastMCP 不是 FastAPI 的子进程，`gunicorn` 拉起多少 worker 都不会派生 MCP 进程。需要对外
暴露 MCP 工具时用 `python run_mcp.py` 单独部署一个进程即可。
:::

::: tip 多 worker 与 Redis
突发限流、幂等标记与检索/得理响应缓存都在 Redis 上，因此多 worker 之间共享同一份计数与
缓存。未配置 `REDIS_URL` 时这些能力按 fail-open 降级（限流与幂等放行、缓存退化为重算），
每日配额仍由 PostgreSQL 兜底。
:::

## Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # SSE 需要关闭缓冲
    location /api/chat {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

## 数据持久化

| 路径 | 内容 | 备份建议 |
|------|------|----------|
| PostgreSQL | 业务、文档、摘要、画像、配额、可观测性与 checkpoint | 定期备份并验证恢复 |
| Qdrant | `legal_knowledge` 法律索引 + `legal_memory` 长期记忆 | 法律索引可重建，记忆需备份 |
| `data/uploads/` | 用户上传的原始文件 | 按需备份 |
| `data/laws/` | 法律原文 | 只读，随代码版本管理 |
| Redis | 检索/得理响应缓存、限流计数、会话元数据、幂等标记 | 无需备份，全部带 TTL 且可重建 |

::: tip Redis 配置建议
Redis 只存可丢弃的热数据，因此可以关闭 RDB/AOF 持久化，并把 `maxmemory-policy` 设为
`allkeys-lru`。所有 key 带 `legal:` 前缀（`REDIS_KEY_PREFIX` 可改），与同实例上的其他应用隔离。
:::

## 健康检查

```bash
curl http://localhost:8000/api/health
# {"status":"ok","provider":"deepseek","database":"ok","redis":"ok"}
```

可用于负载均衡器或监控系统的健康探测。`status` 只反映 PostgreSQL；Redis 降级时整体仍报
`ok`，只有 `redis` 字段变为 `degraded`。
