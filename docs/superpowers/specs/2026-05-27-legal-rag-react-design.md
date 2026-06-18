# Legal Agent RAG + ReAct 增强设计

> 日期: 2026-05-27
> 状态: 设计中

## 背景

当前 Legal Agent 使用硬编码的 10 条法条 + 关键词匹配作为检索工具，无法利用已下载的 25+ 部完整法律文本（~1.4MB）。本次升级将引入 RAG（检索增强生成）能力，在保持现有 ReAct 循环架构的基础上，大幅提升法律咨询的准确性和覆盖面。

## 设计原则

- **低耦合**：模块间通过 Protocol 接口通信，不直接依赖具体实现
- **可扩展**：向量库支持 Chroma/Milvus 双实现，检索策略可插拔
- **中文注释**：所有代码使用企业级中文注释
- **最小侵入**：保持现有图结构、LLM 层、前端不变

## 架构总览

```
用户 Query
    │
    ├─→ 语义检索（bge-small-zh, 向量相似度）→ top_k 候选
    │
    ├─→ 关键词检索（BM25）→ top_k 候选
    │
    └─→ RRF 融合粗排（Reciprocal Rank Fusion, k=60）
              │
              └─→ Rerank 精排（bge-reranker-base, cross-encoder）
                        │
                        └─→ 最终 top_n 结果 → 工具返回给 LLM
```

## 模块结构

```
services/
├── vectorstore/
│   ├── __init__.py          # 工厂函数 get_vectorstore()
│   ├── base.py              # Protocol: LawVectorStore
│   ├── chroma_store.py      # ChromaDB 实现（默认）
│   └── milvus_store.py      # Milvus 实现（预留）
├── retriever/
│   ├── __init__.py          # 工厂函数 init_retriever() / get_retriever()
│   ├── base.py              # Protocol: LawRetriever
│   ├── semantic.py          # 语义检索器（调用 vectorstore）
│   ├── keyword.py           # BM25 关键词检索器
│   ├── hybrid.py            # 混合检索 + RRF 融合
│   └── reranker.py          # Rerank 精排器（cross-encoder）
├── indexer/
│   ├── __init__.py
│   ├── chunker.py           # 法律文本按条款分块
│   └── builder.py           # 索引构建流水线
agent/
├── tools/
│   ├── __init__.py          # 导出 ALL_TOOLS 列表
│   ├── search.py            # legal_search_tool（RAG 主入口）
│   ├── compare.py           # law_compare_tool（法条对比）
│   ├── risk.py              # risk_assess_tool（风险评估）
│   └── review.py            # contract_review_tool（合同审查）
```

## 向量存储层

### Protocol 定义

```python
class LawVectorStore(Protocol):
    def search(self, query_embedding: list[float], top_k: int) -> list[LawChunk]: ...
    def add_chunks(self, chunks: list[LawChunk], embeddings: list[list[float]]) -> None: ...
    def count(self) -> int: ...
```

### 数据模型

```python
@dataclass
class LawChunk:
    law_name: str           # 法律名称，如"民法典"
    hierarchy: str          # 层级路径，如"第三编 合同 > 第一章 一般规定"
    article_no: str         # 条款号，如"第四百九十条"
    content: str            # 条款正文
    chunk_id: str           # 唯一标识
```

### 分块策略

- 按 `第X条` / `第X条之一` 正则切分，每个条款为一个 chunk
- chunk 元数据包含法律名、编/章/节层级路径
- chunk 大小约 200-500 字
- 对于无条款结构的内容（如宪法序言），按段落切分

### 实现选择

- **Chroma**（默认）：嵌入式，零配置，数据存 `data/chroma_db/`
- **Milvus**（预留）：通过 `VECTORSTORE_TYPE=milvus` 切换

## 检索层

### 语义检索

- 模型：`BAAI/bge-small-zh-v1.5`（512 维，sentence-transformers）
- 本地推理，无 API 成本
- 对 query 做 embedding 后在向量库中检索 top_k

### 关键词检索

- 算法：BM25（rank-bm25 库）
- 对所有 chunk 的 content 建立倒排索引
- 启动时构建，常驻内存

### RRF 融合

- 公式：`score(d) = Σ 1/(k + rank_i(d))`，k=60
- 将语义检索和关键词检索的结果融合为统一排序
- 输出 top_20 候选进入精排

### Rerank 精排

- 模型：`BAAI/bge-reranker-base`（cross-encoder）
- 对 RRF top_20 做 query-document 对的相关性打分
- 输出最终 top_5（可配置）

## 工具集

| 工具 | 输入 | 职责 |
|------|------|------|
| `legal_search_tool` | query: str, top_k: int = 5 | 混合检索法条（RAG 主入口） |
| `law_compare_tool` | law_a: str, law_b: str, topic: str | 对比两部法律/条款在某主题上的异同 |
| `risk_assess_tool` | facts: str | 根据事实描述，检索相关法条并评估法律风险 |
| `contract_review_tool` | contract_text: str, focus: str | 审查合同文本，结合法条指出问题 |

- 每个 tool 内部调用 `get_retriever()` 获取法条
- `risk_assess_tool` 和 `contract_review_tool` 内嵌 RAG + LLM 推理
- 工具间无直接依赖，LLM 在 ReAct 循环中自由组合调用

## LangGraph 图变更

保持现有 ReAct 循环拓扑不变：

```
inject_doc → agent → [should_continue: tools | end]
                      tools (ToolNode) → collect_laws → agent
```

变更点：
1. `ToolNode([legal_search_tool])` → `ToolNode(ALL_TOOLS)`
2. `collect_laws` 增强：从所有 ToolMessage 提取检索结果
3. `agent_node` 系统提示词更新：描述 4 个工具的使用场景

不变：图拓扑、inject_doc、checkpointer、SSE 流式输出、前端

## 启动流程

```python
async def lifespan(app):
    init_meta_db()
    cp = init_checkpointer()

    # 新增：初始化检索系统
    retriever = init_retriever()       # 加载 embedding + reranker 模型
    load_or_build_index()              # 检测索引是否存在，不存在则构建

    app.state.graph = build_graph(cp)
    yield
```

- 首次启动：扫描 `data/laws/*.txt` → 分块 → embedding → 写入 Chroma
- 后续启动：检测 `data/chroma_db/` 存在则直接加载
- 重建索引：`python -m services.indexer.builder --rebuild`

## 依赖

```
# 新增
chromadb>=0.5
pymilvus>=2.4
sentence-transformers>=3.0
transformers>=4.40
rank-bm25>=0.2.2
```

## 环境变量

```
EMBEDDING_MODEL=models/bge-small-zh-v1.5
RERANKER_MODEL=models/bge-reranker-base
VECTORSTORE_TYPE=chroma
CHROMA_DB_PATH=data/chroma_db
MILVUS_URI=localhost:19530
RETRIEVER_TOP_K=20
RERANKER_TOP_N=5
RRF_K=60
```

## 验证方案

1. **索引构建**：运行 builder，确认 chunk 数量合理（预期 3000-5000 条）
2. **检索质量**：用典型法律问题测试（"加班费怎么算"、"合同违约金过高"），确认返回相关法条
3. **端到端**：启动服务，通过前端对话验证 RAG 检索 + LLM 回答的完整流程
4. **工具调用**：验证 LLM 能正确选择不同工具（上传合同时用 contract_review_tool）
5. **Rerank 效果**：对比有无 rerank 的检索结果排序质量
