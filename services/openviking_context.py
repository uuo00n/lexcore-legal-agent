"""Runtime context retrieval backed by a real OpenViking server."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Callable

from services.openviking_client import OpenVikingHTTPClient, OpenVikingMatch, OpenVikingSettings
from services.viking_context import VikingContextHit, VikingContextResult, retrieve_viking_context


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenVikingContextConfig:
    """Controls online OpenViking context lookup used by memory_node."""

    enabled: bool = True
    resource_target_uri: str = "viking://resources/laws"
    skill_target_uri: str = ""
    resource_limit: int = 4
    skill_limit: int = 3
    timeout: float = 3.0
    score_threshold: float | None = None
    fallback_local: bool = True
    skill_domain_filter: bool = True
    abstract_chars: int = 180
    overview_chars: int = 420

    @classmethod
    def from_env(cls) -> "OpenVikingContextConfig":
        return cls(
            enabled=_env_bool("OPENVIKING_CONTEXT_ENABLED", True),
            resource_target_uri=os.getenv("OPENVIKING_RESOURCE_TARGET_URI", "viking://resources/laws"),
            skill_target_uri=os.getenv("OPENVIKING_SKILL_TARGET_URI", ""),
            resource_limit=_env_int("OPENVIKING_CONTEXT_RESOURCE_LIMIT", 4),
            skill_limit=_env_int("OPENVIKING_CONTEXT_SKILL_LIMIT", 3),
            timeout=_env_float("OPENVIKING_CONTEXT_TIMEOUT", 3.0),
            score_threshold=_env_optional_float("OPENVIKING_CONTEXT_SCORE_THRESHOLD"),
            fallback_local=_env_bool("OPENVIKING_CONTEXT_FALLBACK_LOCAL", True),
            skill_domain_filter=_env_bool("OPENVIKING_CONTEXT_SKILL_DOMAIN_FILTER", True),
            abstract_chars=_env_int("OPENVIKING_CONTEXT_ABSTRACT_CHARS", 180),
            overview_chars=_env_int("OPENVIKING_CONTEXT_OVERVIEW_CHARS", 420),
        )


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    return int(value) if value not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    value = os.getenv(key)
    return float(value) if value not in (None, "") else default


def _env_optional_float(key: str) -> float | None:
    value = os.getenv(key)
    return float(value) if value not in (None, "") else None


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _hit_from_match(match: OpenVikingMatch, config: OpenVikingContextConfig) -> VikingContextHit:
    overview = _trim(match.overview or match.content, config.overview_chars)
    abstract = _trim(match.abstract or overview or match.content, config.abstract_chars)
    layer = f"L{match.level}" if match.level is not None else "L0/L1/L2"
    return VikingContextHit(
        context_type=match.context_type,
        uri=match.uri,
        layer=layer,
        score=match.score,
        abstract=abstract,
        overview=overview,
    )


def _dedupe_hits(hits: list[VikingContextHit]) -> list[VikingContextHit]:
    deduped: list[VikingContextHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.uri in seen:
            continue
        seen.add(hit.uri)
        deduped.append(hit)
    return deduped


_DOMAIN_SKILL_MARKERS: dict[str, tuple[str, ...]] = {
    "labor": (
        "labor", "劳动", "仲裁", "工资", "薪资", "报酬", "加班费",
        "wage", "salary", "payroll", "overtime", "社保", "arbitration",
    ),
    "consumer_protection": ("consumer", "消费", "退换", "食品", "广告"),
    "civil_procedure": ("procedure", "litigation", "诉讼", "起诉", "证据"),
    "criminal": ("criminal", "刑事", "犯罪", "治安"),
    "company_commercial": ("company", "commercial", "公司", "证券", "破产", "商事"),
    "intellectual_property": ("intellectual", "property", "专利", "著作权", "商标"),
    "real_estate": ("real_estate", "property", "物业", "房产", "土地"),
    "administrative": ("administrative", "行政", "处罚", "许可", "复议"),
    "data_security": ("data", "privacy", "个人信息", "网络安全", "数据"),
    "civil_code": ("civil", "contract", "lease", "deposit", "民法", "合同", "租赁", "押金"),
}


def _resource_domains(matches: list[OpenVikingMatch]) -> set[str]:
    domains: set[str] = set()
    for match in matches:
        found = re.match(r"^viking://resources/laws/([^/]+)(?:/|$)", match.uri)
        if found and found.group(1) != "general":
            domains.add(found.group(1))
    return domains


def _filter_skill_matches_by_resource_domain(
    skill_matches: list[OpenVikingMatch],
    resource_matches: list[OpenVikingMatch],
    *,
    enabled: bool,
) -> list[OpenVikingMatch]:
    if not enabled or not skill_matches:
        return skill_matches
    domains = _resource_domains(resource_matches)
    if not domains:
        return skill_matches

    markers = {
        marker.lower()
        for domain in domains
        for marker in _DOMAIN_SKILL_MARKERS.get(domain, (domain,))
    }
    filtered = []
    for match in skill_matches:
        haystack = " ".join(
            [match.uri, match.abstract, match.overview, match.content, match.match_reason]
        ).lower()
        if any(marker in haystack for marker in markers):
            filtered.append(match)
    return filtered or skill_matches[:1]


def _format_real_prompt(hits: list[VikingContextHit]) -> str:
    if not hits:
        return ""

    lines = [
        "## 真实 OpenViking Context Database（Resource / Skill）",
        "",
        "用途：以下上下文来自真实 OpenViking find，用于判断法律领域、检索范围、处理流程和追问策略；法条引用仍必须以本轮法律检索工具结果为准。",
    ]
    for context_type in ("resource", "skill"):
        typed_hits = [hit for hit in hits if hit.context_type == context_type]
        if not typed_hits:
            continue
        title = "Resource 命中" if context_type == "resource" else "Skill 命中"
        lines.extend(["", f"### {title}"])
        for index, hit in enumerate(typed_hits, start=1):
            lines.append(f"{index}. URI: {hit.uri}")
            lines.append(f"   score: {hit.score:.4f}; layer: {hit.layer}")
            if hit.abstract:
                lines.append(f"   L0: {hit.abstract}")
            if hit.overview:
                lines.append(f"   L1: {hit.overview}")
    return "\n".join(lines)


def retrieve_real_openviking_context(
    query: str,
    *,
    client: OpenVikingHTTPClient,
    config: OpenVikingContextConfig | None = None,
) -> VikingContextResult:
    """Retrieve Resource and Skill context from a real OpenViking server."""
    config = config or OpenVikingContextConfig.from_env()
    if not config.enabled or not query.strip():
        return VikingContextResult(hits=[], prompt="")

    resource_matches = client.find(
        query,
        target_uri=config.resource_target_uri,
        context_type="resource",
        limit=config.resource_limit,
        score_threshold=config.score_threshold,
        level=[0, 1, 2],
    )
    skill_matches = client.find(
        query,
        target_uri=config.skill_target_uri,
        context_type="skill",
        limit=config.skill_limit,
        score_threshold=config.score_threshold,
        level=[0, 1, 2],
    )
    skill_matches = _filter_skill_matches_by_resource_domain(
        skill_matches,
        resource_matches,
        enabled=config.skill_domain_filter,
    )
    matches = [
        match
        for match in [*resource_matches, *skill_matches]
        if match.context_type in {"resource", "skill"} and match.uri
    ]
    hits = _dedupe_hits([_hit_from_match(match, config) for match in matches])
    return VikingContextResult(hits=hits, prompt=_format_real_prompt(hits))


def retrieve_agent_context(
    query: str,
    *,
    thread_id: str,
    profile: str | None = None,
    summary: str | None = None,
    longterm: str | None = None,
    config: OpenVikingContextConfig | None = None,
    client_factory: Callable[[OpenVikingSettings], OpenVikingHTTPClient] | None = None,
) -> VikingContextResult:
    """Retrieve Agent context, preferring real OpenViking and falling back locally."""
    config = config or OpenVikingContextConfig.from_env()

    if config.enabled and query.strip():
        client = None
        try:
            settings = replace(OpenVikingSettings.from_env(), timeout=config.timeout)
            factory = client_factory or OpenVikingHTTPClient
            client = factory(settings)
            real_result = retrieve_real_openviking_context(query, client=client, config=config)
            if real_result.prompt:
                return real_result
        except Exception as exc:
            log.debug("真实 OpenViking 上下文检索失败，回退本地 Context Layer: %s", exc)
        finally:
            if client is not None:
                client.close()

    if config.fallback_local:
        return retrieve_viking_context(
            query,
            thread_id=thread_id,
            profile=profile,
            summary=summary,
            longterm=longterm,
        )
    return VikingContextResult(hits=[], prompt="")
