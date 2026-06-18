"""OpenViking HTTP adapter.

This module talks to a real OpenViking server over its public HTTP API. It does
not import the OpenViking Python package, keeping dependency and license
boundaries explicit for this project.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class OpenVikingSettings:
    """Runtime configuration for the OpenViking HTTP server."""

    base_url: str
    api_key: str = ""
    timeout: float = 120.0
    account: str = ""
    user: str = ""
    actor_peer_id: str = ""

    @classmethod
    def from_env(cls) -> "OpenVikingSettings":
        return cls(
            base_url=os.getenv("OPENVIKING_BASE_URL", "http://localhost:1933"),
            api_key=os.getenv("OPENVIKING_API_KEY", ""),
            timeout=float(os.getenv("OPENVIKING_TIMEOUT", "120")),
            account=os.getenv("OPENVIKING_ACCOUNT", ""),
            user=os.getenv("OPENVIKING_USER", ""),
            actor_peer_id=os.getenv("OPENVIKING_ACTOR_PEER_ID", ""),
        )


@dataclass(frozen=True)
class OpenVikingMatch:
    """Normalized retrieval hit returned by OpenViking find/search."""

    uri: str
    context_type: str
    score: float
    abstract: str = ""
    content: str = ""
    overview: str = ""
    level: int | None = None
    match_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "context_type": self.context_type,
            "score": round(self.score, 4),
            "abstract": self.abstract,
            "overview": self.overview,
            "content": self.content,
            "level": self.level,
            "match_reason": self.match_reason,
        }


class OpenVikingHTTPClient:
    """Small synchronous client for the OpenViking HTTP API."""

    def __init__(
        self,
        settings: OpenVikingSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings or OpenVikingSettings.from_env()
        self._client = httpx.Client(
            base_url=self.settings.base_url.rstrip("/"),
            timeout=self.settings.timeout,
            headers=self._headers(),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.settings.api_key:
            headers["X-API-Key"] = self.settings.api_key
        if self.settings.account:
            headers["X-OpenViking-Account"] = self.settings.account
        if self.settings.user:
            headers["X-OpenViking-User"] = self.settings.user
        if self.settings.actor_peer_id:
            headers["X-OpenViking-Actor-Peer"] = self.settings.actor_peer_id
        return headers

    @staticmethod
    def _result(response: httpx.Response) -> Any:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:1000]
            raise httpx.HTTPStatusError(
                f"{exc}; response={detail}",
                request=exc.request,
                response=exc.response,
            ) from exc
        data = response.json()
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    def health(self) -> dict[str, Any]:
        """Return server health if available."""
        return self._result(self._client.get("/health"))

    def add_resource(
        self,
        path: str | Path,
        *,
        to: str | None = None,
        parent: str | None = None,
        reason: str = "",
        wait: bool = False,
        build_index: bool = True,
    ) -> dict[str, Any]:
        """Add a local file or remote URL as an OpenViking resource."""
        if to and parent:
            raise ValueError("Cannot specify both 'to' and 'parent'.")

        path_value = str(path)
        payload: dict[str, Any]
        local_path = Path(path_value)
        if local_path.exists():
            with local_path.open("rb") as file_obj:
                upload = self._client.post(
                    "/api/v1/resources/temp_upload",
                    files={"file": (local_path.name, file_obj, "application/octet-stream")},
                )
            upload_result = self._result(upload)
            payload = {
                "temp_file_id": upload_result["temp_file_id"],
                "source_name": local_path.name,
            }
        else:
            payload = {"path": path_value}

        if to:
            payload["to"] = to
        if parent:
            payload["parent"] = parent
        if reason:
            payload["reason"] = reason
        payload["wait"] = wait
        # OpenViking 0.3.24 HTTP API refreshes semantics/vectors through the
        # resource add workflow and rejects a public build_index field.
        payload["create_parent"] = True

        return self._result(self._client.post("/api/v1/resources", json=payload))

    def add_skill(self, data: Any, *, wait: bool = False) -> dict[str, Any]:
        """Add a structured OpenViking skill."""
        return self._result(self._client.post("/api/v1/skills", json={"data": data, "wait": wait}))

    def find(
        self,
        query: str,
        *,
        target_uri: str | list[str] = "",
        context_type: str | list[str] | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        level: list[int] | None = None,
    ) -> list[OpenVikingMatch]:
        payload: dict[str, Any] = {
            "query": query,
            "target_uri": target_uri,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        if context_type is not None:
            payload["filter"] = _context_type_filter(context_type)
        if level is not None:
            payload["level"] = level
        result = self._result(self._client.post("/api/v1/search/find", json=payload))
        return _normalize_matches(result)

    def search(
        self,
        query: str,
        *,
        target_uri: str | list[str] = "",
        session_id: str | None = None,
        context_type: str | list[str] | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        level: list[int] | None = None,
    ) -> list[OpenVikingMatch]:
        payload: dict[str, Any] = {
            "query": query,
            "target_uri": target_uri,
            "session_id": session_id,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        if context_type is not None:
            payload["filter"] = _context_type_filter(context_type)
        if level is not None:
            payload["level"] = level
        result = self._result(self._client.post("/api/v1/search/search", json=payload))
        return _normalize_matches(result)

    def abstract(self, uri: str) -> str:
        return str(self._result(self._client.get("/api/v1/content/abstract", params={"uri": uri})))

    def overview(self, uri: str) -> str:
        return str(self._result(self._client.get("/api/v1/content/overview", params={"uri": uri})))

    def read(self, uri: str, *, offset: int = 0, limit: int = -1) -> str:
        return str(self._result(
            self._client.get(
                "/api/v1/content/read",
                params={"uri": uri, "offset": offset, "limit": limit},
            )
        ))

    def write(
        self,
        uri: str,
        content: str,
        *,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Write text content into OpenViking filesystem."""
        payload: dict[str, Any] = {
            "uri": uri,
            "content": content,
            "mode": mode,
            "wait": wait,
        }
        if timeout is not None:
            payload["timeout"] = timeout
        result = self._result(self._client.post("/api/v1/content/write", json=payload))
        return result if isinstance(result, dict) else {"result": result}

    def reindex(self, uri: str, *, mode: str = "vectors_only", wait: bool = True) -> dict[str, Any]:
        """Trigger OpenViking semantic/vector reindexing for a URI scope."""
        result = self._result(
            self._client.post(
                "/api/v1/content/reindex",
                json={"uri": uri, "mode": mode, "wait": wait},
            )
        )
        return result if isinstance(result, dict) else {"result": result}

    def wait_processed(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Wait until OpenViking background processing queues are drained."""
        result = self._result(
            self._client.post(
                "/api/v1/system/wait",
                json={"timeout": timeout},
                timeout=timeout or self.settings.timeout,
            )
        )
        return result if isinstance(result, dict) else {"result": result}


def _normalize_matches(result: Any) -> list[OpenVikingMatch]:
    """Flatten OpenViking FindResult shapes into a simple list."""
    if result is None:
        return []

    if isinstance(result, list):
        return [_match_from_item(item, "") for item in result]

    matches: list[OpenVikingMatch] = []
    if isinstance(result, dict):
        for context_type, key in (
            ("resource", "resources"),
            ("memory", "memories"),
            ("skill", "skills"),
        ):
            for item in result.get(key) or []:
                matches.append(_match_from_item(item, context_type))
    return matches


def _context_type_filter(context_type: str | list[str]) -> dict[str, Any]:
    """Build OpenViking's metadata filter DSL for context type scoping."""
    if isinstance(context_type, str):
        values = [context_type]
    else:
        values = context_type
    return {"op": "must", "field": "context_type", "conds": values}


def _match_from_item(item: Any, fallback_context_type: str) -> OpenVikingMatch:
    if hasattr(item, "to_dict"):
        item = item.to_dict()
    if not isinstance(item, dict):
        item = {"uri": str(item)}
    return OpenVikingMatch(
        uri=str(item.get("uri", "")),
        context_type=str(item.get("context_type") or fallback_context_type),
        score=float(item.get("score") or 0.0),
        abstract=str(item.get("abstract") or ""),
        overview=str(item.get("overview") or ""),
        content=str(item.get("content") or item.get("text") or ""),
        level=item.get("level"),
        match_reason=str(item.get("match_reason") or ""),
    )
