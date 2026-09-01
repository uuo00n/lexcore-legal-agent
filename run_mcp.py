#!/usr/bin/env python3
"""Legal Assistant MCP Server 入口。

启动方式：
    python run_mcp.py              # stdio 模式（Claude Desktop / IDE）
    MCP_TRANSPORT=sse python run_mcp.py  # SSE 模式（远程调用）

测试方式：
    mcp dev run_mcp.py             # MCP Inspector
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from infrastructure.sanitize import RedactingFormatter

_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_log_handler],
)

from mcp_server.startup import initialize_rag
from mcp_server.server import mcp

initialize_rag()

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        mcp.run(
            transport="sse",
            host=os.getenv("MCP_SSE_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_SSE_PORT", "8001")),
        )
    else:
        mcp.run()
