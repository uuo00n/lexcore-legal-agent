"""可选后台 API Key 鉴权。"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException


def admin_auth_enabled() -> bool:
    """
    函数作用：
        判断后台 API Key 鉴权是否启用。
    输入参数：
        - 无
    输出参数：
        - bool
    """
    return bool(os.getenv("ADMIN_API_KEY"))


async def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """
    函数作用：
        FastAPI 依赖：当 ADMIN_API_KEY 存在时校验 X-Admin-Key。
    输入参数：
        - x_admin_key: str | None，默认值 Header(default=None)
    输出参数：
        - None
    """
    expected = os.getenv("ADMIN_API_KEY")
    if not expected:
        return
    if x_admin_key != expected:
        raise HTTPException(401, "invalid admin key")
