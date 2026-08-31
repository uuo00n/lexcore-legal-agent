# WebSearch 移除分析

## 范围与结论

本分析在修改业务代码前完成，覆盖 `agent/`、`mcp_server/`、`services/`、`main.py`、`api/`、`tests/`、依赖文件与 `.env.example`。当前 WebSearch 是本地法律 RAG 质量不足时的联网兜底，并非本地 RAG 的组成部分，可以在不替换 Chroma、BM25、RRF 或 Reranker 的前提下独立移除。

## 1. WebSearch Tool 实现位置

- `agent/tools/web_search.py`：定义 LangChain `web_search_tool`，通过 MCP Client 调用 `web_search_fallback`。
- `mcp_server/tools/web_search.py`：定义 FastMCP `web_search_fallback`；存在 `TAVILY_API_KEY` 时调用 Tavily，否则调用 DuckDuckGo。

调用链为：

`legal_consult_agent` → `web_search_tool` → `services.mcp_client.call_tool` → FastMCP `web_search_fallback` → Tavily / DuckDuckGo。

## 2. 绑定 WebSearch 的 Agent

- `agent/tools/__init__.py` 的 `LEGAL_CONSULT_TOOLS` 将 `web_search_tool` 绑定给法律咨询 Agent。
- 同文件的 `ALL_TOOLS` 将其注册给 LangGraph 的通用 `ToolNode`。
- `agent/nodes.py::_legal_consult_tools_for_state` 在本地法律检索前隐藏 WebSearch；本地检索执行后隐藏本地检索并开放 WebSearch。因此实际可调用方是 `legal_consult_agent`。
- Fact Agent 与 Contract Agent 没有直接绑定 WebSearch。

## 3. 指示联网搜索的 Prompt / Tool Result

- `agent/prompts.py::LEGAL_SYSTEM_PROMPT` 指示本地检索无结果、低质量、明显无关或分数低于阈值时调用 `web_search_tool`。
- `mcp_server/tools/search.py::legal_search` 在 `no_relevant_result` 和 `low_quality` 返回中指示调用 `web_search_tool`。
- 运行文档中的旧架构说明也描述了联网兜底，但它们不参与运行时调用。

## 4. Tool Registry 注册情况

- `agent/tools/__init__.py::ALL_TOOLS` 注册 `web_search_tool`，供 `agent/graph.py` 创建的 `ToolNode` 执行。
- `agent/tools/__init__.py::LEGAL_CONSULT_TOOLS` 注册 `web_search_tool`，供法律咨询 Agent 动态 `bind_tools`。
- 没有发现其他 `TOOL_REGISTRY`、`TOOL_MAP` 或等价运行时注册表包含 WebSearch。

## 5. FastMCP 暴露情况

- `mcp_server/server.py` 导入 `mcp_server.tools.web_search`，导入时由 `@mcp.tool()` 注册 `web_search_fallback`。
- FastMCP 的 server instructions 明确列出“联网搜索”。
- 主 Graph 没有独立 `web_search_node`、`internet_search_node` 或搜索 fallback node；WebSearch 经现有通用 `ToolNode` 执行。移除时必须保留 `ToolNode`。

## 6. 依赖 WebSearch 的测试

- `tests/test_agent_guardrails.py`：断言 WebSearch 位于全局工具集、法律咨询工具集、Prompt 和动态绑定结果中。
- `tests/test_legal_search_scores.py`：断言低质量本地结果的 hint 推荐 `web_search_tool`。
- `tests/test_mcp_client.py`：并发限制测试使用 `web_search_fallback` 作为无资源组的普通工具名；测试并不需要真实联网，但名称依赖 WebSearch。

这些断言需要改为验证仅存在受信数据源工具、低质量时返回 `evidence_insufficient`，以及 WebSearch 名称无法进入注册表或主要 Prompt。

## 7. 仅供 WebSearch 使用的第三方依赖

- `duckduckgo-search`：仅由 `mcp_server/tools/web_search.py` 延迟导入。
- `tavily-python`：仅由 `mcp_server/tools/web_search.py` 延迟导入。

全仓调用检查未发现其他用途，因此两项均可从 `requirements.txt` 删除。

## 8. WebSearch 环境变量

- `.env.example` 中的 `TAVILY_API_KEY` 仅供联网搜索工具使用，可删除。
- 未发现 `SERPAPI_API_KEY`、`GOOGLE_SEARCH_KEY`、`BING_SEARCH_KEY` 或其他搜索引擎密钥。

## 9. 删除影响评估

- 法律咨询 Agent 将不再在本地 RAG 低质量后联网兜底；证据不足必须明确返回 `evidence_insufficient=true`，不得猜测法条。
- `ALL_TOOLS` 和 FastMCP 工具清单会减少 WebSearch，但通用 `ToolNode`、MCP Server 和其他法律工具继续保留。
- 本地 DOC RAG 的索引、Embedding、Chroma、BM25、RRF、Reranker 与初始化路径不需要删除或替换。
- 旧测试和运行文档中的联网兜底描述需要同步更新，避免文档继续承诺已移除的能力。
- 新增 Delilegal 法规与类案检索后，可信来源为 Delilegal OpenAPI 和 Local DOC RAG；二者均无结果时停止检索并报告证据不足，不存在 Internet fallback。
