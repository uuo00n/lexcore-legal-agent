# RAG 检索流程

## 时序图

```mermaid
sequenceDiagram
    participant Agent as LangGraph Agent
    participant MCP as MCP Server
    participant HYDE as HyDE 模块
    participant OL as Ollama (qwen2.5:1.5b)
    participant SEM as 语义检索器
    participant BM25 as BM25 检索器
    participant EMB as bge-small-zh-v1.5
    participant CHROMA as ChromaDB
    participant RRF as RRF 融合
    participant RR as Reranker (bge-reranker-base)

    Agent->>MCP: call_tool("legal_search", {query, top_k})

    Note over MCP,OL: 查询增强（HYDE_ENABLED=true 时）
    MCP->>HYDE: retrieve(query)
    HYDE->>OL: 问题重写 prompt
    OL-->>HYDE: 重写后的 query
    HYDE->>OL: HyDE 生成 prompt
    OL-->>HYDE: 假设性法条文本

    Note over HYDE,CHROMA: 双路检索
    HYDE->>SEM: retrieve(hyde_doc, top_k=20)
    SEM->>EMB: encode(hyde_doc)
    EMB-->>SEM: embedding vector
    SEM->>CHROMA: ANN search
    CHROMA-->>SEM: 语义候选集

    HYDE->>BM25: retrieve(rewritten_query, top_k=20)
    BM25-->>HYDE: 关键词候选集

    Note over HYDE,RR: 融合 + 精排
    HYDE->>RRF: fuse(semantic, keyword)
    RRF-->>HYDE: 融合排序结果

    HYDE->>RR: rerank(original_query, fused[:20])
    RR-->>HYDE: scored results

    Note over HYDE: 阈值过滤 (score >= 0.3)
    HYDE-->>MCP: filtered LawChunks

    alt 有相关结果
        MCP-->>Agent: {"status": "found", "results": [...]}
    else 无相关结果
        MCP-->>Agent: {"status": "no_relevant_result", "results": []}
        Note over Agent: Agent 可尝试 Delilegal；仍无依据则 evidence_insufficient=true
    end
```

## 三路查询策略

| 检索路径 | 输入 | 目的 |
|----------|------|------|
| 语义检索 (ChromaDB) | HyDE 假设文档 | 缩小口语与法条的语义鸿沟 |
| 关键词检索 (BM25) | 重写后的 query | 精确匹配法律术语 |
| 精排 (Reranker) | 原始 query | 真实相关性判断 |

## 查询增强详解

### 问题重写

将用户口语化表达转换为精确的法律检索 query：

```
输入: "房东不退押金咋办"
输出: "如何处理房东不退还押金的情况"
```

### HyDE 假设文档

生成一段假设性法条文本，语义上接近真实法条：

```
输入: "房东不退押金咋办"
输出: "根据《中华人民共和国民法典》第九百七十八条：出租人应当按照约定将租赁物交付给承租人..."
```

## 无结果处理

当 Reranker 分数全部低于阈值（默认 0.3）时：
1. 返回 `{"status": "no_relevant_result"}`
2. Agent 根据 prompt 中的决策流程判断：
   - 用户问题信息不足 → 追问用户
   - 问题明确但本地库未覆盖 → 可调用 `search_law_tool` 或 `search_case_tool`
   - 可信来源均无结果 → 返回 `evidence_insufficient=true`，不得编造依据
