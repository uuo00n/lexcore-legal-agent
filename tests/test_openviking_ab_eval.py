from __future__ import annotations

from dataclasses import dataclass

from eval.openviking_ab import rerank_chunks_by_openviking_scope, run_openviking_ab_eval
from services.openviking_client import OpenVikingMatch


@dataclass
class FakeChunk:
    chunk_id: str
    law_name: str = ""
    content: str = ""


class FakeRetriever:
    def retrieve(self, query: str, top_k: int = 5):
        candidates = [
            FakeChunk("民法典_第五百七十七条", law_name="民法典"),
            FakeChunk("劳动合同法_第二十条", law_name="劳动合同法"),
        ]
        return candidates[:top_k]


class FakeOpenVikingClient:
    def find(self, query: str, *, target_uri="", context_type=None, limit=5, level=None):
        return [
            OpenVikingMatch(
                uri="viking://resources/laws/labor/劳动合同法.txt",
                context_type="resource",
                score=0.88,
                abstract="劳动争议资源入口",
                content="劳动合同法 第二十条 试用期工资",
            )
        ]


def test_run_openviking_ab_eval_compares_baseline_and_real_openviking_variant():
    result = run_openviking_ab_eval(
        [
            {
                "question": "试用期三个月，公司只发八成工资合法吗？",
                "ground_truth_contexts": ["劳动合同法_第二十条"],
                "acceptable_contexts": ["劳动合同法_第二十条"],
                "corpus_status": "in_corpus",
            }
        ],
        retriever=FakeRetriever(),
        openviking_client=FakeOpenVikingClient(),
        top_k=1,
    )

    assert result["mode"] == "openviking_ab"
    assert result["baseline"]["aggregated"]["hit_rate"] == 0.0
    assert result["openviking"]["aggregated"]["hit_rate"] == 1.0
    assert result["delta"]["hit_rate"] == 1.0
    assert result["openviking_routing"]["resource_hit_rate"] == 1.0
    assert result["details"][0]["openviking_candidate_ids"] == [
        "民法典_第五百七十七条",
        "劳动合同法_第二十条",
    ]
    assert result["details"][0]["openviking_retrieved_ids"] == ["劳动合同法_第二十条"]
    assert result["details"][0]["openviking_matches"][0]["uri"] == (
        "viking://resources/laws/labor/劳动合同法.txt"
    )


def test_rerank_chunks_prefers_exact_article_resource_before_law_level_scope():
    chunks = [
        FakeChunk("劳动合同法_第二十一条", law_name="劳动合同法"),
        FakeChunk("劳动合同法_第二十条", law_name="劳动合同法"),
        FakeChunk("民法典_第五百七十七条", law_name="民法典"),
    ]
    matches = [
        OpenVikingMatch(
            uri="viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md",
            context_type="resource",
            score=0.92,
            abstract="劳动合同法 第二十条 试用期工资不得低于约定工资的百分之八十。",
        )
    ]

    reranked = rerank_chunks_by_openviking_scope(chunks, matches)

    assert [chunk.chunk_id for chunk in reranked] == [
        "劳动合同法_第二十条",
        "劳动合同法_第二十一条",
        "民法典_第五百七十七条",
    ]


def test_rerank_chunks_keeps_strong_baseline_article_when_openviking_top_is_near_miss():
    chunks = [
        FakeChunk("劳动合同法_第二十条", law_name="劳动合同法"),
        FakeChunk("劳动合同法_第八十二条", law_name="劳动合同法"),
        FakeChunk("劳动合同法_第八十三条", law_name="劳动合同法"),
    ]
    matches = [
        OpenVikingMatch(
            uri="viking://resources/laws/labor/劳动合同法/劳动合同法_第八十三条.md",
            context_type="resource",
            score=0.69,
            abstract="劳动合同法 第八十三条 违法约定试用期。",
        ),
        OpenVikingMatch(
            uri="viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md",
            context_type="resource",
            score=0.67,
            abstract="劳动合同法 第二十条 试用期工资不得低于约定工资的百分之八十。",
        ),
    ]

    reranked = rerank_chunks_by_openviking_scope(chunks, matches)

    assert [chunk.chunk_id for chunk in reranked] == [
        "劳动合同法_第二十条",
        "劳动合同法_第八十三条",
        "劳动合同法_第八十二条",
    ]
