"""OpenViking 风格上下文层。

本模块先在本地实现 OpenViking 的核心工程思想：
- Resource / Memory / Skill 三类上下文
- viking:// URI
- L0 abstract + L1 overview 的分层加载

它不强依赖 OpenViking server，便于本项目离线运行和测试；后续可以把
retrieve_viking_context 的实现替换为 OpenViking SDK/MCP 的 find/search/read。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CatalogEntry:
    """可检索的 Resource 或 Skill 目录项。"""

    context_type: str
    uri: str
    keywords: tuple[str, ...]
    abstract: str
    overview: str
    priority: float = 0.0


@dataclass(frozen=True)
class VikingContextHit:
    """一次 OpenViking 风格上下文命中。"""

    context_type: str
    uri: str
    layer: str
    score: float
    abstract: str
    overview: str

    def to_dict(self) -> dict:
        return {
            "context_type": self.context_type,
            "uri": self.uri,
            "layer": self.layer,
            "score": round(self.score, 4),
            "abstract": self.abstract,
        }


@dataclass(frozen=True)
class VikingContextResult:
    """上下文检索结果，包含可注入 prompt 的文本。"""

    hits: list[VikingContextHit]
    prompt: str


_RESOURCE_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/labor/",
        keywords=("劳动", "试用期", "辞退", "解除", "欠薪", "工资", "社保", "仲裁", "工伤", "加班"),
        abstract="劳动争议资源入口，覆盖劳动关系、工资、解除、试用期、社保和仲裁路径。",
        overview=(
            "用于定位劳动纠纷相关资料。优先关注劳动关系是否成立、解除原因、工资基数、"
            "工作年限、合同和社保情况；法条引用仍必须通过本轮法律检索工具确认。"
        ),
        priority=0.08,
    ),
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/civil_code/contract/",
        keywords=("合同", "违约", "押金", "租赁", "房东", "租房", "借款", "定金", "买卖"),
        abstract="民法典合同编资源入口，覆盖租赁、借款、买卖、违约责任和押金争议。",
        overview=(
            "用于定位合同和租赁争议。重点检查合同约定、履行情况、违约事实、证据材料、"
            "损失计算和解除/返还请求。"
        ),
        priority=0.06,
    ),
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/civil_code/tort/",
        keywords=("侵权", "赔偿", "人身损害", "伤害", "霸凌", "名誉", "肖像", "隐私"),
        abstract="民法典侵权责任资源入口，覆盖人身损害、校园霸凌、名誉肖像和一般侵权。",
        overview=(
            "用于定位侵权责任问题。重点确认侵权行为、损害后果、因果关系、过错、证据和责任主体。"
        ),
    ),
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/marriage_family/",
        keywords=("离婚", "夫妻", "婚姻", "抚养", "彩礼", "财产分割", "子女"),
        abstract="婚姻家事资源入口，覆盖离婚、子女抚养、夫妻财产和彩礼返还。",
        overview=(
            "用于定位婚姻家事问题。重点确认婚姻状态、财产来源、登记情况、子女年龄和实际抚养情况。"
        ),
    ),
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/consumer_protection/",
        keywords=("消费者", "退款", "退货", "商家", "网购", "食品安全", "虚假宣传"),
        abstract="消费者权益保护资源入口，覆盖退换货、虚假宣传、食品安全和平台交易纠纷。",
        overview=(
            "用于定位消费纠纷。重点确认交易凭证、商品或服务问题、沟通记录、平台规则和损失范围。"
        ),
    ),
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/criminal/",
        keywords=("刑事", "诈骗", "盗窃", "拘留", "报警", "犯罪", "伤情", "立案"),
        abstract="刑事风险资源入口，覆盖诈骗、盗窃、伤害、报警、拘留和立案风险。",
        overview=(
            "用于定位刑事风险。重点确认行为方式、金额或伤情、主观目的、是否报警和是否已被采取强制措施。"
        ),
        priority=0.04,
    ),
    CatalogEntry(
        context_type="resource",
        uri="viking://resources/laws/civil_procedure/",
        keywords=("起诉", "诉讼", "法院", "证据", "立案", "执行", "保全", "管辖"),
        abstract="民事程序资源入口，覆盖起诉、证据、管辖、保全、执行和诉讼路径。",
        overview=(
            "用于定位程序性问题。重点确认请求基础、证据清单、被告信息、管辖法院和时效风险。"
        ),
    ),
)

_SKILL_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/labor_arbitration_workflow/",
        keywords=("劳动", "试用期", "辞退", "解除", "欠薪", "仲裁", "赔偿", "加班"),
        abstract="劳动仲裁咨询流程：补事实、列证据、检索法条、判断请求和风险。",
        overview=(
            "流程：1. 先确认劳动关系、工作年限、工资和解除原因；2. 列劳动合同、工资流水、"
            "考勤、通知书、聊天记录等证据；3. 再检索劳动合同法和仲裁相关规则；"
            "4. 输出可主张请求、风险和下一步材料清单。"
        ),
        priority=0.12,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/wage_dispute_workflow/",
        keywords=(
            "工资", "薪资", "劳动报酬", "欠薪", "拖欠", "克扣", "少发", "补发",
            "加班费", "最低工资", "试用期工资", "提成", "绩效", "奖金", "工资条",
            "工资流水", "离职工资", "两个月发一次", "调休",
        ),
        abstract="工资争议咨询流程：分类工资问题、补充工资事实、核算请求、列证据和维权路径。",
        overview=(
            "流程：1. 先区分欠薪、克扣、延迟发放、试用期工资、最低工资、加班费、提成奖金、"
            "离职结算和补偿基数；2. 补充城市、劳动关系、工资结构、支付周期、欠付期间、"
            "考勤加班和解除/离职状态；3. 检索劳动法、劳动合同法、仲裁时效、工资支付和地方规则后再引用；"
            "4. 按项目列金额公式、证据、风险和劳动监察/仲裁等下一步路径。"
        ),
        priority=0.20,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/deposit_dispute_workflow/",
        keywords=("押金", "租赁", "房东", "租房", "退租", "损坏", "交接"),
        abstract="押金退还纠纷流程：确认合同、交接、损坏和证据，再给维权路径。",
        overview=(
            "流程：1. 确认租赁合同和押金条款；2. 确认退租交接、房屋损坏和扣款理由；"
            "3. 收集转账记录、聊天记录、照片视频、交接单；4. 判断协商、投诉、起诉路径。"
        ),
        priority=0.10,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/contract_review_checklist/",
        keywords=("合同", "审查", "条款", "违约", "付款", "解除", "责任"),
        abstract="合同审查清单：主体、标的、价款、履行、违约、解除、争议解决。",
        overview=(
            "流程：按主体资格、核心义务、付款节点、验收标准、违约责任、解除条件、"
            "管辖和补充材料逐项审查，并输出风险等级和修改建议。"
        ),
        priority=0.09,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/evidence_collection_checklist/",
        keywords=("证据", "录音", "聊天记录", "转账", "照片", "证明", "材料"),
        abstract="证据收集清单：围绕主体、事实、损失、沟通和时间线整理材料。",
        overview=(
            "流程：把证据分为身份/关系、合同或约定、履行过程、损失后果、沟通记录、"
            "时间线六类；提醒用户保留原始载体和形成时间。"
        ),
        priority=0.05,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/video_screenshot/",
        keywords=("视频", "录屏", "截图", "抽帧", "截帧", "微信聊天录屏", "聊天录屏", "证据视频", "关键帧"),
        abstract="视频截图提取：从聊天录屏或证据视频中抽取关键截图、去重并生成 SHA256 报告。",
        overview=(
            "流程：1. 确认视频来源、格式和证明目的；2. 调用视频证据处理接口抽取关键帧，"
            "默认使用场景变化检测和图像去重；3. 生成 frames/ 截图和 _report.json，记录时间戳、"
            "SHA256、抽帧策略和去重统计；4. 将截图作为事实证据材料整理，不把视频内容当作法条依据；"
            "5. 需要提交法院或仲裁时提醒保留原始视频载体和完整聊天上下文。"
        ),
        priority=0.18,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/limitation_period_reasoning/",
        keywords=("时效", "诉讼时效", "超过", "多久", "期限", "仲裁时效"),
        abstract="时效判断流程：确认请求类型、起算点、中断中止和特殊期限。",
        overview=(
            "流程：先确认请求权类型，再确认知道或应当知道权利受损时间，检查催告、还款、"
            "协商、投诉、仲裁或起诉等中断事由，最后提示时效风险。"
        ),
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/lawsuit_filing_workflow/",
        keywords=("起诉", "立案", "法院", "诉状", "被告", "执行", "保全"),
        abstract="民事起诉流程：确定请求、被告、证据、管辖和诉状材料。",
        overview=(
            "流程：1. 明确诉讼请求和事实理由；2. 确认被告身份信息；3. 整理证据目录；"
            "4. 判断管辖和费用；5. 输出起诉材料清单。"
        ),
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/legal_pleading_drafter/",
        keywords=(
            "起诉状", "诉状", "诉讼书", "法律文书", "诉讼文书", "答辩状", "上诉状",
            "申请书", "执行申请书", "保全申请书", "证据目录", "诉讼请求", "事实与理由",
            "去 AI 腔", "去AI腔", "AI 腔",
        ),
        abstract="法律文书生成：起诉状、答辩状、上诉状、申请书和证据目录的事实核对、结构生成与去 AI 腔。",
        overview=(
            "流程：1. 先确认文书类型和诉讼角色；2. 区分已确认事实、用户陈述、证据支持事实、"
            "待补事实和法律判断；3. 生成诉讼请求、事实与理由、证据目录等固定结构；"
            "4. 用克制、证据导向的法律文书语言去 AI 腔；5. 不得虚构事实、案号、法院、"
            "法条、金额、日期或证据，并提示管辖、时效、金额和证据缺口。"
        ),
        priority=0.16,
    ),
    CatalogEntry(
        context_type="skill",
        uri="viking://skills/legal/pdf_processor/",
        keywords=("PDF", "pdf", "扫描", "扫描件", "OCR", "ocr", "图片", "文字层", "上传", "页码", "合同PDF"),
        abstract="PDF 文档处理流程：检测文字层、识别扫描件、OCR fallback、按页抽取文本。",
        overview=(
            "流程：1. 先检查 PDF 页数和每页可抽取文本量；2. 有文字层时直接按页提取，"
            "保留 page-level 文本用于合同条款定位、证据页码和材料审查；3. 扫描件或图片 PDF "
            "需要 OCR，优先调用 ocrmypdf，未安装时明确提示 OCR 不可用；4. OCR 后重新抽取文本，"
            "禁止在未读出内容时声称已完成 PDF 审查。"
        ),
        priority=0.14,
    ),
)


def _safe_uri_part(value: str) -> str:
    value = value.strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _score_entry(query: str, entry: CatalogEntry) -> float:
    hits = sum(1 for keyword in entry.keywords if keyword and keyword in query)
    if hits == 0:
        return 0.0
    return min(0.99, 0.42 + hits * 0.09 + entry.priority)


def _catalog_hits(query: str, catalog: Iterable[CatalogEntry], limit: int) -> list[VikingContextHit]:
    hits: list[VikingContextHit] = []
    for entry in catalog:
        score = _score_entry(query, entry)
        if score <= 0:
            continue
        hits.append(VikingContextHit(
            context_type=entry.context_type,
            uri=entry.uri,
            layer="L0/L1",
            score=score,
            abstract=entry.abstract,
            overview=entry.overview,
        ))
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:limit]


def _memory_hits(
    *,
    thread_id: str,
    profile: str | None,
    summary: str | None,
    longterm: str | None,
) -> list[VikingContextHit]:
    safe_thread = _safe_uri_part(thread_id)
    hits: list[VikingContextHit] = []
    if profile:
        hits.append(VikingContextHit(
            context_type="memory",
            uri=f"viking://memory/cases/{safe_thread}/profile.md",
            layer="L0/L1",
            score=0.74,
            abstract=_trim(profile, 120),
            overview=profile,
        ))
    if summary:
        hits.append(VikingContextHit(
            context_type="memory",
            uri=f"viking://memory/cases/{safe_thread}/summary.md",
            layer="L0/L1",
            score=0.70,
            abstract=_trim(summary, 120),
            overview=summary,
        ))
    if longterm:
        hits.append(VikingContextHit(
            context_type="memory",
            uri=f"viking://memory/cases/{safe_thread}/longterm.md",
            layer="L0/L1",
            score=0.78,
            abstract=_trim(longterm, 120),
            overview=longterm,
        ))
    return hits


def _format_prompt(hits: list[VikingContextHit]) -> str:
    if not hits:
        return ""

    labels = {
        "resource": "Resource",
        "memory": "Memory",
        "skill": "Skill",
    }
    lines = [
        "## OpenViking Context Layer（Resource / Memory / Skill）",
        "",
        "说明：以下是用于定位资料、案件记忆和处理流程的 L0/L1 上下文，不作为法条引用依据；明确法条仍必须来自本轮法律检索工具。",
    ]
    for context_type in ("resource", "memory", "skill"):
        group = [hit for hit in hits if hit.context_type == context_type]
        if not group:
            continue
        lines.extend(["", f"### {labels[context_type]}"])
        for hit in group:
            lines.extend([
                f"- URI: {hit.uri}",
                f"  - Layer: {hit.layer}",
                f"  - L0: {hit.abstract}",
                f"  - L1: {_trim(hit.overview, 260)}",
            ])
    return "\n".join(lines)


def retrieve_viking_context(
    query: str,
    *,
    thread_id: str,
    profile: str | None = None,
    summary: str | None = None,
    longterm: str | None = None,
    resource_limit: int = 2,
    skill_limit: int = 2,
) -> VikingContextResult:
    """检索 OpenViking 风格上下文。

    这是一个确定性本地实现：用关键词先完成目录定位，输出 viking:// URI 和
    L0/L1 内容。真实 OpenViking 接入时，可替换为 client.find/client.search。
    """
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return VikingContextResult(hits=[], prompt="")

    hits = []
    hits.extend(_catalog_hits(normalized_query, _RESOURCE_CATALOG, resource_limit))
    hits.extend(_memory_hits(
        thread_id=thread_id,
        profile=profile,
        summary=summary,
        longterm=longterm,
    ))
    hits.extend(_catalog_hits(normalized_query, _SKILL_CATALOG, skill_limit))
    hits.sort(key=lambda item: item.score, reverse=True)
    return VikingContextResult(hits=hits, prompt=_format_prompt(hits))


def _default_root() -> Path:
    return Path(os.getenv("VIKING_CONTEXT_ROOT", "data/viking_context"))


def save_case_workspace(
    thread_id: str,
    messages: list[dict],
    *,
    root: str | Path | None = None,
) -> Path:
    """将对话沉淀为 OpenViking 风格案件工作区。

    写入目录：
    data/viking_context/memory/cases/{thread_id}/
      .abstract.md
      .overview.md
      conversation.md
    """
    base = Path(root) if root is not None else _default_root()
    safe_thread = _safe_uri_part(thread_id)
    case_dir = base / "memory" / "cases" / safe_thread
    case_dir.mkdir(parents=True, exist_ok=True)

    cleaned: list[tuple[str, str]] = []
    for message in messages:
        role = str(message.get("role", "")).strip() or "unknown"
        content = _trim(str(message.get("content", "")), 800)
        if content:
            cleaned.append((role, content))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (case_dir / "conversation.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {now}\n")
        for role, content in cleaned:
            label = {"human": "用户", "ai": "助手", "system": "系统"}.get(role, role)
            fh.write(f"- {label}: {content}\n")

    first_user = next((content for role, content in cleaned if role == "human"), "")
    abstract = f"法律咨询案件记忆：{_trim(first_user or thread_id, 90)}"
    overview_lines = [
        f"# 案件记忆概览：{safe_thread}",
        "",
        "## 最近对话",
    ]
    for role, content in cleaned[-8:]:
        label = {"human": "用户", "ai": "助手", "system": "系统"}.get(role, role)
        overview_lines.append(f"- {label}: {content}")

    (case_dir / ".abstract.md").write_text(abstract + "\n", encoding="utf-8")
    (case_dir / ".overview.md").write_text("\n".join(overview_lines) + "\n", encoding="utf-8")
    return case_dir
