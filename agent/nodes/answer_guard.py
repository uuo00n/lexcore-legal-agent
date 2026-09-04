"""让最终答复留在已核验证据内的确定性护栏（§P2、§二 问题 10）。

重构前的做法是：让模型自由生成，再用字符串把没核验过的引用替换成
「（未在本轮检索结果中确认的法条引用已移除）」。用户于是看到一条疤痕，
而不是一份可用的答复。

现在改成三段式：
1. ``audit_answer_citations`` 用确定性规则检查草稿里写出的每一处法条／案号是否命中
   ``verified_evidence``；命中判定复用 Citation Verifier 的 canonical 匹配，
   因此《劳动合同法》第八十五条与「劳动合同法(2012修正) 第八十五条」算同一条（P0-2）。
2. 没通过就带着允许引用清单重新生成一次（§P2「只从已核验状态重新生成」）。
3. 仍然不通过则退回确定性重建：正文按句丢弃带未核验引用的句子，法条与类案小节
   直接由已核验证据渲染——不留任何替换标记。

``user_safe_risks`` 负责另一半：核验问题原文里带着内部 Agent 名、step_id 与被判定
编造的引用，属于 §P2 明令不得对用户暴露的内容，所以风险小节只按问题 ``type``
输出面向用户的中文表述。
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.state import VerificationResult

from .citation_verifier import (
    CASE_NO_RE,
    LAW_CITATION_RE,
    build_evidence_index,
    match_case,
    match_law,
)

# 句子切分：中文句末标点与换行都算边界，切分后保留标点，便于原样重组。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。；;！!？?\n])")

# 核验问题 → 面向用户的风险表述。不得回显问题原文（含 Agent 名、step_id、编造引用）。
_RISK_STATEMENTS: dict[str, str] = {
    "plan_incomplete": "本轮仍有分析环节没有完成，结论可能不完整。",
    "citation_invalid": "有法条或案例未能在本轮检索中得到确认，已不作为依据使用。",
    "obsolete_law": "涉及的法规可能已失效或被修订，适用前需核对现行有效文本。",
    "obsolete_law_risk": "涉及的法规可能已失效或被修订，适用前需核对现行有效文本。",
    "retrieval_insufficient": "本轮检索到的法律依据不足以支撑完整结论。",
    "case_evidence_insufficient": "本轮没有检索到足够的类案，司法实践口径仅供参考。",
    "reasoning_conflict": "不同分析结论之间存在分歧，需要结合完整材料再判断。",
    "overconfident": "现有事实不足以支撑确定结论，以下判断只是可能性分析。",
    "answer_format_error": "答复结构可能不完整。",
}
_RISK_FALLBACK = "本轮核验发现结论仍存在不确定性，需要结合完整事实与现行有效法律文本再判断。"


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


@dataclass(frozen=True)
class DraftCitationAudit:
    """一份草稿答复的引用核验结果。"""

    ungrounded_laws: tuple[str, ...] = ()
    ungrounded_cases: tuple[str, ...] = ()

    @property
    def grounded(self) -> bool:
        return not self.ungrounded_laws and not self.ungrounded_cases

    @property
    def labels(self) -> tuple[str, ...]:
        return (*self.ungrounded_laws, *self.ungrounded_cases)


def _evidence_index(laws: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]]):
    """按已核验证据建索引；引用匹配口径与 Citation Verifier 完全一致。"""
    return build_evidence_index({
        "retrieved_laws": [dict(item) for item in laws or []],
        "retrieved_cases": [dict(item) for item in cases or []],
    })


def audit_answer_citations(
    content: str,
    laws: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> DraftCitationAudit:
    """列出草稿里没有被已核验证据支持的法条引用与案号。"""
    if not content:
        return DraftCitationAudit()
    index = _evidence_index(laws, cases)
    ungrounded_laws: list[str] = []
    for law_name, article_no in LAW_CITATION_RE.findall(content):
        evidence, _reason = match_law({"law_name": law_name, "article_no": article_no}, index)
        label = f"《{law_name}》{article_no}"
        if evidence is None and label not in ungrounded_laws:
            ungrounded_laws.append(label)
    ungrounded_cases: list[str] = []
    for case_no in CASE_NO_RE.findall(content):
        evidence, _reason = match_case({"case_no": case_no}, index)
        if evidence is None and case_no not in ungrounded_cases:
            ungrounded_cases.append(case_no)
    return DraftCitationAudit(
        ungrounded_laws=tuple(ungrounded_laws),
        ungrounded_cases=tuple(ungrounded_cases),
    )


def allowed_citation_labels(
    laws: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> list[str]:
    """重新生成时交给模型的允许引用清单：只有这些写法可以出现在答复里。"""
    labels: list[str] = []
    for item in laws or []:
        law_name = _text(item.get("display_law_name") or item.get("law_name") or item.get("title"))
        article_no = _text(item.get("article_no"))
        if not law_name:
            continue
        label = f"《{law_name}》{article_no}"
        if label not in labels:
            labels.append(label)
    for item in cases or []:
        case_no = _text(item.get("case_no") or item.get("case_number"))
        if case_no and case_no not in labels:
            labels.append(case_no)
    return labels


def keep_grounded_sentences(
    text: str,
    laws: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> str:
    """确定性重建用：整句丢弃带未核验引用的句子，不留替换标记。

    句子是能保持语义完整的最小单位——按字符替换会留下断句，按段落丢弃会连正确
    结论一起丢掉。全部句子都带未核验引用时返回空串，由调用方改用中性表述。
    """
    if not text:
        return ""
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence.strip():
            continue
        if audit_answer_citations(sentence, laws, cases).grounded:
            kept.append(sentence)
    return "".join(kept).strip()


def user_safe_risks(verification: VerificationResult | Mapping[str, Any] | None) -> list[str]:
    """把结构化核验问题翻译成面向用户的风险表述（§P2 不暴露核验内部信息）。"""
    if not verification:
        return []
    statements: list[str] = []
    issue_types = [
        _text(issue.get("type"))
        for issue in verification.get("structured_issues", []) or []
        if isinstance(issue, Mapping)
    ]
    for issue_type in issue_types:
        statement = _RISK_STATEMENTS.get(issue_type)
        if statement and statement not in statements:
            statements.append(statement)
    if statements:
        return statements[:5]
    # 旧 checkpoint 只有字符串列表、或问题类型未覆盖时，退回一句中性表述，
    # 仍然不回显原文。
    if not verification.get("passed", True) or any(
        verification.get(key) for key in ("issues", "missing_sources", "invalid_citations")
    ):
        return [_RISK_FALLBACK]
    return []
