"""FastMCP 服务器定义 —— 注册所有法律助手工具。"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Legal Assistant",
    instructions="中国法律能力标准化暴露层 — 法规与类案检索、法律对比、风险评估、合同审查、诉讼时效计算、文书生成",
)

# 注册所有工具（import 时自动通过 @mcp.tool() 装饰器注册）
from mcp_server.tools import search  # noqa: F401, E402
from mcp_server.tools import compare  # noqa: F401, E402
from mcp_server.tools import risk  # noqa: F401, E402
from mcp_server.tools import review  # noqa: F401, E402
from mcp_server.tools import limitations  # noqa: F401, E402
from mcp_server.tools import jurisdiction  # noqa: F401, E402
from mcp_server.tools import draft  # noqa: F401, E402
