"""最终答复质量度量：``answer_score`` 的唯一产出口（§P2）。

重构前 ``score_legal_answer`` 在三处各算一次——Answer Generator 的 trace 事件、
Intent Router 的直答分支、以及 ``api/chat.py`` 顶层的 ``analyze_legal_message``。
同一份答复因此可能拿到两份互不一致的评分（§二 问题 12、§一「禁止两套并行的最终评分」）。

现在评分只在生成答复的那个节点算一次，结果写进 State 的 ``answer_score``；
``api/chat.py`` 直接复用该结果写入 ``legal_analysis.answer_score``，不再重算。
评分规则本身仍然复用 ``services.legal_analysis.score_legal_answer``，避免出现第二套口径。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.legal_analysis import score_legal_answer


@dataclass(frozen=True)
class FinalQualityMetrics:
    """一次最终答复的结构化质量度量。"""

    score: int
    checks: dict[str, bool]
    citations: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """State 与 trace 使用的可序列化形态，字段与旧版 ``answer_score`` 一致。"""
        return {
            "score": self.score,
            "checks": dict(self.checks),
            "citations": dict(self.citations),
        }


def measure_final_answer(
    question: str,
    answer: str,
    verified_laws: list[dict[str, Any]] | None = None,
) -> FinalQualityMetrics:
    """按已核验法条度量最终答复；``verified_laws`` 必须来自 ``verified_evidence``（P0-1）。"""
    raw = score_legal_answer(question, answer, verified_laws or [])
    return FinalQualityMetrics(
        score=int(raw.get("score") or 0),
        checks=dict(raw.get("checks") or {}),
        citations=dict(raw.get("citations") or {}),
    )
