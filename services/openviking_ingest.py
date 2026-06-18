"""Import legal resources and legal workflow skills into OpenViking."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from services.indexer.chunker import chunk_law_file


@dataclass(frozen=True)
class LawResourceSpec:
    """One local law file and its target OpenViking resource URI."""

    path: Path
    law_name: str
    domain: str
    to_uri: str
    reason: str


@dataclass(frozen=True)
class LawArticleResourceSpec:
    """One law article card and its target OpenViking resource URI."""

    law_name: str
    article_no: str
    chunk_id: str
    domain: str
    to_uri: str
    content: str
    reason: str


_DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("labor", ("劳动法", "劳动合同法", "劳动争议调解仲裁法", "社会保险法")),
    ("consumer_protection", ("消费者权益保护法", "食品安全法", "广告法")),
    ("civil_procedure", ("民事诉讼法",)),
    ("criminal", ("刑法", "刑事诉讼法", "治安管理处罚法", "反间谍法", "国家安全法")),
    ("company_commercial", ("公司法", "企业破产法", "证券法", "票据法", "商业银行法", "海商法")),
    ("intellectual_property", ("专利法", "著作权法", "商标法")),
    ("real_estate", ("物业管理条例", "土地管理法", "城市房地产管理法")),
    ("administrative", ("行政处罚法", "行政许可法", "行政复议法", "行政诉讼法", "行政强制法")),
    ("data_security", ("个人信息保护法", "网络安全法", "数据安全法")),
    ("civil_code", ("民法典", "合同法_历史版本", "物权法_历史版本")),
)


def build_law_resource_specs(laws_dir: str | Path) -> list[LawResourceSpec]:
    """Build deterministic OpenViking resource targets for local law files."""
    base = Path(laws_dir)
    specs: list[LawResourceSpec] = []
    for path in sorted(base.glob("*.txt")):
        law_name = _extract_law_name(path)
        domain = _law_domain(law_name)
        specs.append(LawResourceSpec(
            path=path,
            law_name=law_name,
            domain=domain,
            to_uri=f"viking://resources/laws/{domain}/{path.name.split('_', 1)[-1]}",
            reason=f"中国法律语料导入：{law_name}",
        ))
    return specs


def import_law_resources(
    client: Any,
    laws_dir: str | Path = "data/laws",
    *,
    wait: bool = False,
    build_index: bool = True,
    include_domains: set[str] | None = None,
) -> dict[str, Any]:
    """Import law text files into OpenViking resources."""
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for spec in build_law_resource_specs(laws_dir):
        if include_domains and spec.domain not in include_domains:
            skipped.append({"law_name": spec.law_name, "domain": spec.domain})
            continue
        result = client.add_resource(
            spec.path,
            to=spec.to_uri,
            reason=spec.reason,
            wait=wait,
            build_index=build_index,
        )
        imported.append({
            "law_name": spec.law_name,
            "domain": spec.domain,
            "to_uri": spec.to_uri,
            "result": result,
        })
    return {"imported": len(imported), "skipped": len(skipped), "items": imported}


def build_law_article_resource_specs(
    laws_dir: str | Path,
    *,
    max_articles_per_law: int | None = None,
) -> list[LawArticleResourceSpec]:
    """Build article-level OpenViking resource cards from local law files."""
    base = Path(laws_dir)
    specs: list[LawArticleResourceSpec] = []
    for path in sorted(base.glob("*.txt")):
        law_name = _extract_law_name(path)
        domain = _law_domain(law_name)
        article_count = 0
        for chunk in chunk_law_file(path):
            if max_articles_per_law is not None and article_count >= max_articles_per_law:
                break
            article_count += 1
            chunk_id = str(chunk.chunk_id)
            article_no = str(chunk.article_no)
            specs.append(LawArticleResourceSpec(
                law_name=law_name,
                article_no=article_no,
                chunk_id=chunk_id,
                domain=domain,
                to_uri=f"viking://resources/laws/{domain}/{law_name}/{chunk_id}.md",
                content=_article_card_content(
                    law_name=law_name,
                    domain=domain,
                    chunk_id=chunk_id,
                    article_no=article_no,
                    hierarchy=str(chunk.hierarchy or ""),
                    article_text=str(chunk.content or ""),
                ),
                reason=f"中国法律法条资源导入：{chunk_id}",
            ))
    return specs


def import_law_article_resources(
    client: Any,
    laws_dir: str | Path = "data/laws",
    *,
    wait: bool = False,
    build_index: bool = True,
    include_domains: set[str] | None = None,
    max_articles_per_law: int | None = None,
    mode: str = "create",
    timeout: float | None = None,
    reindex_uri: str = "viking://resources/laws",
    reindex_mode: str = "vectors_only",
    write_wait: bool | None = None,
) -> dict[str, Any]:
    """Import article-level law cards into OpenViking resources."""
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    effective_write_wait = wait if write_wait is None else write_wait
    for spec in build_law_article_resource_specs(
        laws_dir,
        max_articles_per_law=max_articles_per_law,
    ):
        if include_domains and spec.domain not in include_domains:
            skipped.append({
                "chunk_id": spec.chunk_id,
                "law_name": spec.law_name,
                "domain": spec.domain,
            })
            continue
        result = _write_article_card(
            client,
            spec.to_uri,
            spec.content,
            mode=mode,
            wait=effective_write_wait,
            timeout=timeout,
        )
        imported.append({
            "chunk_id": spec.chunk_id,
            "law_name": spec.law_name,
            "article_no": spec.article_no,
            "domain": spec.domain,
            "to_uri": spec.to_uri,
            "result": result,
        })

    reindex_result = None
    reindex = getattr(client, "reindex", None)
    if build_index and imported and callable(reindex):
        reindex_result = reindex(reindex_uri, mode=_normalize_reindex_mode(reindex_mode), wait=wait)

    return {
        "imported": len(imported),
        "skipped": len(skipped),
        "items": imported,
        "reindex_result": reindex_result,
    }


def _normalize_reindex_mode(mode: str) -> str:
    """Normalize project-friendly reindex aliases to OpenViking API modes."""
    return "semantic_and_vectors" if mode == "all" else mode


def _write_article_card(
    client: Any,
    uri: str,
    content: str,
    *,
    mode: str,
    wait: bool,
    timeout: float | None,
) -> dict[str, Any]:
    """Write an article card, supporting project-level upsert semantics."""
    if mode != "upsert":
        return client.write(uri, content, mode=mode, wait=wait, timeout=timeout)
    try:
        return client.write(uri, content, mode="replace", wait=wait, timeout=timeout)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        return client.write(uri, content, mode="create", wait=wait, timeout=timeout)


def build_legal_skill_specs() -> list[dict[str, Any]]:
    """Return structured OpenViking skills for legal consultation workflows."""
    return [
        _skill(
            "labor-arbitration-workflow",
            "劳动仲裁咨询流程：补事实、列证据、检索法条、判断请求和风险。",
            [
                "先确认劳动关系、入职时间、试用期约定、工资基数、解除或降薪原因。",
                "整理劳动合同、工资流水、考勤、解除通知、聊天记录、社保记录。",
                "再检索劳动合同法、劳动法、劳动争议调解仲裁法相关规则。",
                "输出可主张请求、证据缺口、仲裁时效和下一步材料清单。",
            ],
            tags=["legal", "labor", "arbitration"],
        ),
        _skill(
            "wage-dispute-workflow",
            "工资争议咨询流程：分类工资问题、补充工资事实、核算请求、列证据和维权路径。",
            [
                "先区分欠薪、克扣、延迟发放、试用期工资、最低工资、加班费、提成奖金、离职结算和补偿基数。",
                "补充城市、劳动关系、工资结构、支付周期、欠付期间、考勤加班、离职或解除状态和现有证据。",
                "使用法律检索确认劳动法、劳动合同法、劳动争议调解仲裁法、工资支付和地方规则后再引用。",
                "按请求项目列明期间、基数、倍率或公式、证据、金额和不确定项，不虚构工资或加班数据。",
                "输出证据缺口、仲裁时效风险、劳动监察投诉、劳动仲裁和后续诉讼等路径。",
            ],
            tags=["legal", "labor", "wage", "salary", "payroll", "overtime"],
        ),
        _skill(
            "deposit-dispute-workflow",
            "押金退还纠纷流程：确认合同、交接、损坏和证据，再给维权路径。",
            [
                "确认租赁合同、押金条款、退租时间和交接记录。",
                "区分自然损耗、实际损坏、合同约定扣款和房东单方扣款。",
                "收集转账记录、聊天记录、照片视频、交接单和维修报价。",
                "判断协商、平台投诉、社区调解、小额诉讼或普通起诉路径。",
            ],
            tags=["legal", "contract", "lease"],
        ),
        _skill(
            "contract-review-checklist",
            "合同审查清单：主体、标的、价款、履行、违约、解除、争议解决。",
            [
                "核对主体资格、授权代表、合同标的和关键定义。",
                "检查价款、付款节点、交付验收、违约责任和解除条件。",
                "定位管辖、通知、保密、知识产权和补充协议条款。",
                "按高/中/低风险输出问题和修改建议。",
            ],
            tags=["legal", "contract", "review"],
        ),
        _skill(
            "evidence-collection-checklist",
            "证据收集清单：围绕主体、事实、损失、沟通和时间线整理材料。",
            [
                "把证据分为身份关系、合同约定、履行过程、损失后果、沟通记录、时间线。",
                "提醒保留原始载体、形成时间、完整聊天上下文和转账凭证。",
                "区分直接证据、间接证据、待补证据和可能灭失证据。",
                "输出证据目录和下一步补证动作。",
            ],
            tags=["legal", "evidence"],
        ),
        _skill(
            "video-screenshot",
            "视频截图提取：从聊天录屏或证据视频中抽取关键截图、去重并生成 SHA256 报告。",
            [
                "确认视频文件、来源、证明目的和是否需要保留完整上下文。",
                "调用视频证据处理接口抽取关键帧，默认使用场景变化检测、内容区 dHash 和 SHA256 去重。",
                "输出 frames/ 截图和 _report.json，记录视频时长、截图时间戳、SHA256、抽帧策略和去重统计。",
                "把截图作为事实证据材料交给证据整理、文书生成或法律咨询链路，法条依据仍必须来自法律检索工具。",
                "提醒用户保留原始视频载体、聊天账号身份、完整上下文和生成报告，避免只提交零散截图。",
            ],
            tags=["legal", "evidence", "video", "screenshot"],
        ),
        _skill(
            "limitation-period-reasoning",
            "时效判断流程：确认请求类型、起算点、中断中止和特殊期限。",
            [
                "先确认请求权类型和适用的一般或特殊时效。",
                "确认知道或应当知道权利受损以及义务人的时间。",
                "检查催告、还款、协商、投诉、仲裁、起诉等中断事由。",
                "提示时效风险、仍可协商空间和需要补充的时间线证据。",
            ],
            tags=["legal", "procedure", "limitation"],
        ),
        _skill(
            "lawsuit-filing-workflow",
            "民事起诉流程：确定请求、被告、证据、管辖和诉状材料。",
            [
                "明确诉讼请求、事实理由、金额计算和法律关系。",
                "确认被告身份信息、送达地址和管辖法院。",
                "整理证据目录、起诉状、身份证明、授权材料和费用。",
                "判断是否需要财产保全、先行调解或小额诉讼程序。",
            ],
            tags=["legal", "procedure", "litigation"],
        ),
        _skill(
            "legal-pleading-drafter",
            "法律文书生成：起诉状、答辩状、上诉状、申请书和证据目录的事实核对、结构生成与去 AI 腔。",
            [
                "先确认文书类型、诉讼角色、法院阶段和是否存在管辖或时效风险。",
                "区分已确认事实、用户陈述、证据支持事实、待补事实和法律判断，不得虚构事实。",
                "按文书类型生成诉讼请求、事实与理由、申请事项、答辩请求或证据目录等固定结构。",
                "用克制、具体、证据导向的法律文书语言去 AI 腔，保留必要法律格式和风险提示。",
                "输出待补信息、证据对应关系和风险提示，尤其标明金额、日期、法院、案号、法条和证据缺口。",
            ],
            tags=["legal", "procedure", "litigation", "pleading", "drafting"],
        ),
        _skill(
            "pdf-processor",
            "PDF 文档处理流程：检测文字层、识别扫描件、OCR fallback、按页抽取文本。",
            [
                "先检查 PDF 页数、是否加密、每页可抽取文本量和是否 scanned-like。",
                "普通文字层 PDF 直接抽取 page-level text，保留 1-based 页码。",
                "扫描件、图片 PDF、盖章合同或法院材料需要 OCR；优先调用 ocrmypdf。",
                "本地缺少 OCR 依赖时必须明确说明无法 OCR，不得假装已读取扫描件内容。",
                "OCR 成功后再抽取文本，并把页码文本交给合同审查、证据审查或法律咨询链路。",
            ],
            tags=["legal", "pdf", "ocr", "document", "page-level"],
        ),
    ]


def import_legal_skills(client: Any, *, wait: bool = False) -> dict[str, Any]:
    """Import legal workflow skills into OpenViking."""
    imported = []
    for skill in build_legal_skill_specs():
        imported.append({"name": skill["name"], "result": client.add_skill(skill, wait=wait)})
    return {"imported": len(imported), "items": imported}


def _skill(name: str, description: str, steps: list[str], *, tags: list[str]) -> dict[str, Any]:
    content_lines = [f"# {name}", "", description, "", "## Workflow"]
    content_lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    return {
        "name": name,
        "description": description,
        "content": "\n".join(content_lines),
        "tags": tags,
    }


def _extract_law_name(path: Path) -> str:
    stem = path.stem
    parts = stem.split("_", 1)
    return parts[1] if len(parts) == 2 else stem


def _article_card_content(
    *,
    law_name: str,
    domain: str,
    chunk_id: str,
    article_no: str,
    hierarchy: str,
    article_text: str,
) -> str:
    lines = [
        f"# {chunk_id}",
        "",
        f"law_name: {law_name}",
        f"article_no: {article_no}",
        f"chunk_id: {chunk_id}",
        f"domain: {domain}",
    ]
    if hierarchy:
        lines.append(f"hierarchy: {hierarchy}")
    lines.extend(["", article_text])
    return "\n".join(lines).strip()


def _law_domain(law_name: str) -> str:
    normalized = re.sub(r"\s+", "", law_name)
    for domain, names in _DOMAIN_RULES:
        if any(name == normalized for name in names):
            return domain
    return "general"
