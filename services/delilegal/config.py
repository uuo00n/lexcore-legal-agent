"""从环境变量加载得理服务配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from services.delilegal.exceptions import DelilegalConfigurationError


@dataclass(frozen=True, slots=True)
class DelilegalSettings:
    """得理连接配置；实例本身不会输出或记录 API Key。"""

    base_url: str = ""
    api_key: str = ""
    law_search_path: str | None = "/api/v1/generice/law/list"
    case_search_path: str = "/api/v1/generice/case/list"
    connect_timeout: float = 5.0
    read_timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "DelilegalSettings":
        default_search_path = "/api/v1/generice/law/list"
        law_path = (
            os.getenv("DELILEGAL_LAW_SEARCH_PATH", default_search_path).strip()
            or default_search_path
        )
        return cls(
            base_url=os.getenv("DELILEGAL_BASE_URL", "").strip(),
            api_key=os.getenv("DELILEGAL_API_KEY", "").strip(),
            law_search_path=law_path,
            case_search_path=os.getenv(
                "DELILEGAL_CASE_SEARCH_PATH", "/api/v1/generice/case/list"
            ).strip(),
            connect_timeout=float(os.getenv("DELILEGAL_CONNECT_TIMEOUT", "5")),
            read_timeout=float(os.getenv("DELILEGAL_READ_TIMEOUT", "30")),
        )

    def validate_credentials(self) -> None:
        if not self.api_key:
            raise DelilegalConfigurationError(
                "Delilegal credentials are not configured; set DELILEGAL_API_KEY."
            )

    def validate_base_url(self) -> None:
        if not self.base_url:
            raise DelilegalConfigurationError(
                "Delilegal base URL is not configured; set DELILEGAL_BASE_URL."
            )

    def endpoint(self, endpoint_type: str) -> str:
        if endpoint_type == "law_search":
            path = self.law_search_path
            variable = "DELILEGAL_LAW_SEARCH_PATH"
        elif endpoint_type == "case_search":
            path = self.case_search_path
            variable = "DELILEGAL_CASE_SEARCH_PATH"
        else:
            raise DelilegalConfigurationError("Unknown Delilegal endpoint type.")
        if not path:
            raise DelilegalConfigurationError(
                f"Delilegal endpoint is not configured; set {variable}."
            )
        return path if path.startswith("/") else f"/{path}"
