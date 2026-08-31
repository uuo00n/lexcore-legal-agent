# 部署文档

## 生产环境要求

| 资源 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 5 GB | 10 GB |
| Python | 3.12+ | 3.12+ |
| Ollama | 0.24+ | 0.24+ |

::: tip 内存说明
bge-small-zh-v1.5 和 bge-reranker-base 各占约 400MB 内存，Ollama qwen2.5:1.5b 占约 1GB。
:::

## 部署步骤

### 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# .env
LLM_PROVIDER=zhipu
ZHIPU_API_KEY=sk-xxx
LLM_MODEL=glm-4.7

EMBEDDING_MODEL=models/bge-small-zh-v1.5
RERANKER_MODEL=models/bge-reranker-base

HYDE_ENABLED=true
HYDE_MODEL=qwen2.5:1.5b
HYDE_LLM_BASE_URL=http://localhost:11434/v1

RETRIEVER_TOP_K=20
RERANKER_SCORE_THRESHOLD=0.3
RRF_K=60
MAX_TOOL_CALLS=4

# 得理法律开放平台
DELILEGAL_BASE_URL=https://openapi.delilegal.com
DELILEGAL_APP_ID=your_app_id
DELILEGAL_SECRET=your_secret
DELILEGAL_LAW_SEARCH_PATH=/api/qa/v3/search/queryListLaw
DELILEGAL_CASE_SEARCH_PATH=/api/qa/v3/search/queryListCase

DOCS_DB=data/docs.sqlite
UPLOAD_DIR=data/uploads
MAX_UPLOAD_MB=10
```

### 3. 启动 Ollama

```bash
ollama serve &
ollama pull qwen2.5:1.5b
```

### 4. 构建索引

```bash
python -m services.indexer.builder
```

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
由于 MCP Server 作为子进程运行，多 worker 模式下每个 worker 会启动独立的 MCP Server 实例。建议 worker 数不超过 CPU 核数。
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
| `data/chroma_db/` | 法条向量索引 + 长期记忆 | 可重建，建议备份记忆 |
| `data/docs.sqlite` | 线程元数据 + 上传文档文本 | 定期备份 |
| `data/uploads/` | 用户上传的原始文件 | 按需备份 |
| `data/laws/` | 法律原文 | 只读，随代码版本管理 |

## 健康检查

```bash
curl http://localhost:8000/api/health
# {"status":"ok","provider":"zhipu"}
```

可用于负载均衡器或监控系统的健康探测。
