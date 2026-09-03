# Legal Agent Trusted Sources

本阶段之后，Legal Agent 只允许使用两类法律事实与依据来源：得理法律开放平台，以及仓库内已经索引的本地 DOC 法律知识库。

```text
            Legal Agent
               /      \
              /        \
      Delilegal       Local DOC
       OpenAPI           RAG
      /      \
   Laws       Cases
```

## Delilegal OpenAPI

Agent 不直接发送 HTTP 请求。调用链固定为：

`Legal Agent → LangChain Tool → services.delilegal.DelilegalClient → Delilegal OpenAPI`

- `search_law_tool` 查询法律法规、地方性法规、司法解释等规范性内容。法规全文先经过相关条文提取，只向模型返回相关条款、必要上下文、效力 metadata 和 source id。
- `search_case_tool` 查询相似真实裁判案例。完整裁判文书先压缩为基本事实、争议焦点、法院说理、裁判结果、法律依据和必要的证据摘要。
- 法规路径配置为 `/api/v1/generice/law/list`，类案路径配置为 `/api/v1/generice/case/list`。
- 法规和案例检索文本通过 `query` 提交；分页及排序使用 `pageNo`、`pageSize`、`sortField`、`sortOrder`。

凭据只从 `DELILEGAL_API_KEY` 读取，并通过 `Authorization: Bearer <API Key>` 发送，不得进入 Tool Result、日志或 Trace。调用日志只记录 trace id、endpoint type、耗时、成功状态、结果数量和错误类型。

## Local DOC RAG

调用链为：

`Legal Agent → retrieve_local_law_tool → services.search.search_local_law_service → LocalLegalRetriever → HybridRetriever`

`retrieve_local_law_tool` 直接调用进程内 RAG Service Layer，不经过 MCP Client；FastMCP 的
`search_local_law` / `legal_search` 复用同一 Service Layer，作为面向外部客户端的独立暴露层。
`LocalLegalRetriever` 仅是适配层，DOC ingestion、Embedding、Qdrant、BM25、RRF 和 Reranker 均保持不变。

## Evidence Insufficient

当 Delilegal 与 Local DOC RAG 都没有提供充分依据时，工具或 Agent 状态必须保留 `evidence_insufficient=true`。模型可以组织语言，但不得编造具体法条、司法解释、案例、法院裁判或法律效力。

## 禁止的来源

- No Web Search
- No Internet Search Fallback
- 得理失败后不使用搜索引擎兜底
- 本地 RAG 无结果后不使用搜索引擎兜底
