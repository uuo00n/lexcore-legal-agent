# RAG 检索流程

## 时序图

```mermaid
sequenceDiagram
    participant Agent as Specialist Agent
    participant SVC as search_local_law_service
    participant CACHE as Redis 检索缓存
    participant HR as HybridRetriever
    participant HYDE as HyDE 模块
    participant FL as deepseek-v4-flash
    participant SEM as 语义检索器
    participant BM25 as BM25 检索器
    participant EMB as bge-small-zh-v1.5
    participant QDRANT as Qdrant
    participant RRF as RRF 融合
    participant RR as Reranker (bge-reranker-base)

    Agent->>SVC: retrieve_local_law_tool(query, top_k=5)
    Note over Agent,SVC: 进程内直接调用，不经过 MCP Client
    SVC->>HR: retrieve(query, top_k, trace_id)

    HR->>CACHE: GET cache:retrieval:{query 摘要}:{参数指纹}
    alt 缓存命中
        CACHE-->>HR: 已排序结果
    else 缓存未命中或 Redis 降级
        Note over HR,FL: 查询增强（HYDE_ENABLED=true 且非精确法条查询）
        HR->>HYDE: rewrite_query + generate_hypothetical_doc
        HYDE->>FL: 问题重写 prompt
        FL-->>HYDE: 重写后的 query
        HYDE->>FL: HyDE 生成 prompt
        FL-->>HYDE: 假设性法条文本
        HYDE-->>HR: (rewritten_query, hyde_document)

        Note over HR,QDRANT: 双路多变体检索
        HR->>SEM: retrieve([hyde_doc, query, rewritten], top_k=10)
        SEM->>EMB: encode(每个变体)
        EMB-->>SEM: embedding vector
        SEM->>QDRANT: legal_knowledge ANN search
        QDRANT-->>SEM: 语义候选集
        SEM-->>HR: 合并去重后的语义候选

        HR->>BM25: retrieve([query, rewritten, hyde_doc], top_k=10)
        BM25-->>HR: 关键词候选集

        Note over HR: 剔除已废止 / 历史版本条文
        HR->>RRF: reciprocal_rank_fusion_scored(k=60)
        RRF-->>HR: 融合排序结果

        HR->>RR: rerank(原始 query, fused[:20], top_n=5)
        RR-->>HR: scored results
        HR->>CACHE: SET 结果（TTL 1800s）
    end

    Note over HR: 阈值过滤 (score >= 0.3，至少保留 min_results 条)
    HR-->>SVC: LawChunks + 分数

    alt 有高分结果
        SVC-->>Agent: {"status": "found", "results": [...]}
    else 最高分低于阈值
        SVC-->>Agent: {"status": "low_quality", "evidence_insufficient": true, "hint": "..."}
    else 完全无命中
        SVC-->>Agent: {"status": "no_relevant_result", "evidence_insufficient": true}
        Note over Agent: Agent 可改用 search_law_tool / search_case_tool；<br/>仍无依据则报告证据不足
    end
```

## 三路查询策略

| 检索路径 | 输入 | 目的 |
|----------|------|------|
| 语义检索 (Qdrant) | HyDE 假设文档 + 原始 query + 重写 query | 缩小口语与法条的语义鸿沟 |
| 关键词检索 (BM25) | 原始 query + 重写 query + HyDE 文档 | 精确匹配法律术语 |
| 精排 (Reranker) | 原始 query | 真实相关性判断 |

两路都对多个查询变体各跑一次并合并去重，因此口语化提问与法律术语提问都有机会命中同一条法条。
只有一路有结果时跳过融合直接降级（`vector_fallback` / `bm25_fallback`），Qdrant 不可用时整条链
退化为纯 BM25 而不是报错。

## 查询增强详解

HyDE 与问题重写共用一个轻量模型，默认 `deepseek-v4-flash`（`HYDE_MODEL`、`HYDE_LLM_BASE_URL`），
复用 `DEEPSEEK_API_KEY`。`HYDE_BACKEND=hf_lora` 可切到本地 Qwen + LoRA 权重。
命中「精确法条查询」（例如直接问某法第几条）时跳过增强，避免把确定的检索目标改写坏。

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
输出: "根据《中华人民共和国民法典》第七百一十四条：承租人应当按照约定的方法妥善使用租赁物..."
```

## 缓存与可观测性

整条管线包在一层 Redis 缓存里，key 只含归一化 query 的摘要与检索参数指纹（`final_top_k`、
`vector_top_k`、`bm25_top_k`、`rrf_k`、reranker/BM25 是否可用、`include_superseded`），
所以调参后不会命中旧结果。Redis 不可用时缓存层返回未命中，检索照常执行。

每个阶段都会写 trace 事件：`vector_hits`、`bm25_hits`、`fused_hits`、`reranker_hits`，
外加一条 `rag_summary`（延迟、结果数、是否缓存命中）。因此
`/api/admin/traces/{trace_id}/timeline` 上可以逐段看到候选集怎么变化的。

## 无结果处理

Reranker 分数全部低于阈值（`RERANKER_SCORE_THRESHOLD`，默认 0.3）时，`retrieve()` 仍会保留
`RETRIEVAL_MIN_RESULTS`（默认 1）条，避免「候选存在却返回空」这种无法归因的结果，
但 Service 层会把 `status` 标为 `low_quality` 并置 `evidence_insufficient=true`。

Agent 据此按 prompt 中的决策流程判断：

- 用户问题信息不足 → 追问用户
- 问题明确但本地库未覆盖 → 调用 `search_law_tool` 或 `search_case_tool`（得理开放平台）
- 可信来源均无结果 → 返回 `evidence_insufficient=true`，不得编造依据
