"""联网搜索兜底 MCP 工具 —— 本地法库无法回答时通过网络搜索补充信息。"""
from __future__ import annotations

import json
import os

from mcp_server.server import mcp


@mcp.tool()
def web_search_fallback(query: str, max_results: int = 5) -> str:
    """
    函数作用：
        当本地法律数据库无法回答时，通过网络搜索获取补充信息。
    输入参数：
        - query: str
        - max_results: int，默认值 5
    输出参数：
        - str
    """
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        return _search_tavily(query, max_results, tavily_key)
    return _search_duckduckgo(query, max_results)


def _search_duckduckgo(query: str, max_results: int) -> str:
    """
    函数作用：
        使用 DuckDuckGo 搜索（无需 API key）。
    输入参数：
        - query: str
        - max_results: int
    输出参数：
        - str
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return json.dumps(
            {"error": "duckduckgo-search 未安装，请运行: pip install duckduckgo-search"},
            ensure_ascii=False,
        )

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="cn-zh", max_results=max_results))
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {str(e)}"}, ensure_ascii=False)

    formatted = [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in results
    ]
    return json.dumps(formatted, ensure_ascii=False)


def _search_tavily(query: str, max_results: int, api_key: str) -> str:
    """
    函数作用：
        使用 Tavily 搜索（需要 API key，质量更高）。
    输入参数：
        - query: str
        - max_results: int
        - api_key: str
    输出参数：
        - str
    """
    try:
        from tavily import TavilyClient
    except ImportError:
        return _search_duckduckgo(query, max_results)

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results, search_depth="basic")
    except Exception as e:
        return json.dumps({"error": f"Tavily 搜索失败: {str(e)}"}, ensure_ascii=False)

    formatted = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in response.get("results", [])
    ]
    return json.dumps(formatted, ensure_ascii=False)
