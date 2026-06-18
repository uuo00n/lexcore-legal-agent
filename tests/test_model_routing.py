from __future__ import annotations

from services.model_routing import select_model_route


def test_selects_fast_route_for_simple_question(monkeypatch):
    monkeypatch.delenv("LLM_ROUTE_FAST_MODEL", raising=False)

    route = select_model_route(user_message="你好，今天星期几")

    assert route.name == "fast"
    assert route.complexity_score < 5


def test_selects_strong_route_for_complex_legal_question(monkeypatch):
    monkeypatch.setenv("LLM_ROUTE_STRONG_MODEL", "deepseek-v4-pro")

    route = select_model_route(user_message="公司违法辞退我，我要起诉并主张赔偿，需要准备哪些证据")

    assert route.name == "strong"
    assert route.model == "deepseek-v4-pro"
    assert route.complexity_score >= 5


def test_selects_long_route_for_long_document(monkeypatch):
    monkeypatch.setenv("LLM_ROUTE_LONG_PROVIDER", "qwen")

    route = select_model_route(user_message="帮我审查合同", doc_text="合同" * 4000)

    assert route.name == "long"
    assert route.provider == "qwen"
