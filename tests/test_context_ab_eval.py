from __future__ import annotations

from dataclasses import dataclass

from eval.context_ab import (
    build_context_augmented_query,
    disabled_query_enhancement,
    infer_expected_context,
    run_context_ab_eval,
)
from services.viking_context import retrieve_viking_context


@dataclass
class FakeChunk:
    chunk_id: str


class FakeRetriever:
    def retrieve(self, query: str, top_k: int = 5):
        if "劳动争议资源入口" in query or "labor_arbitration_workflow" in query:
            return [FakeChunk("劳动合同法_第四十六条")]
        return [FakeChunk("民法典_第五百七十七条")]


def test_infer_expected_context_from_labor_item():
    expected = infer_expected_context({
        "question": "试用期被公司辞退，能申请劳动仲裁吗？",
        "acceptable_contexts": ["劳动合同法_第四十六条"],
    })

    assert "viking://resources/laws/labor/" in expected.resource_uris
    assert "viking://skills/legal/labor_arbitration_workflow/" in expected.skill_uris


def test_build_context_augmented_query_includes_uri_and_l0():
    context = retrieve_viking_context(
        "试用期被公司辞退，能申请劳动仲裁吗？",
        thread_id="eval-1",
    )

    augmented = build_context_augmented_query("试用期被公司辞退，能申请劳动仲裁吗？", context)

    assert "原始问题" in augmented
    assert "viking://resources/laws/labor/" in augmented
    assert "劳动争议资源入口" in augmented


def test_run_context_ab_eval_compares_baseline_and_context_variant():
    result = run_context_ab_eval(
        [
            {
                "question": "试用期被公司辞退，能申请劳动仲裁吗？",
                "ground_truth_contexts": ["劳动合同法_第四十六条"],
                "acceptable_contexts": ["劳动合同法_第四十六条"],
                "corpus_status": "in_corpus",
            }
        ],
        retriever=FakeRetriever(),
        top_k=1,
    )

    assert result["mode"] == "context_ab"
    assert result["baseline"]["aggregated"]["hit_rate"] == 0.0
    assert result["context_layer"]["aggregated"]["hit_rate"] == 1.0
    assert result["delta"]["hit_rate"] == 1.0
    assert result["context_routing"]["resource_hit_rate"] == 1.0
    assert result["context_routing"]["skill_hit_rate"] == 1.0
    assert result["details"][0]["context_query"] != result["details"][0]["baseline_query"]


def test_disabled_query_enhancement_restores_env(monkeypatch):
    monkeypatch.setenv("HYDE_ENABLED", "true")
    monkeypatch.delenv("HYDE_REWRITE_ENABLED", raising=False)

    with disabled_query_enhancement():
        import os
        assert os.environ["HYDE_ENABLED"] == "false"
        assert os.environ["HYDE_REWRITE_ENABLED"] == "false"

    import os
    assert os.environ["HYDE_ENABLED"] == "true"
    assert "HYDE_REWRITE_ENABLED" not in os.environ
