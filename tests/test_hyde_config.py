from __future__ import annotations

from services.retriever.hyde import _get_enhance_llm


def test_hyde_defaults_to_glm46v_with_zhipu_openai_endpoint(monkeypatch):
    monkeypatch.delenv("HYDE_MODEL", raising=False)
    monkeypatch.delenv("HYDE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("HYDE_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-test-key")

    llm = _get_enhance_llm()

    assert llm.model_name == "glm-4.6v"
    assert llm.openai_api_base == "https://open.bigmodel.cn/api/paas/v4"
    assert llm.openai_api_key.get_secret_value() == "zhipu-test-key"


def test_hyde_can_still_be_switched_back_to_local_ollama_qwen(monkeypatch):
    monkeypatch.setenv("HYDE_MODEL", "qwen2.5:1.5b")
    monkeypatch.setenv("HYDE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("HYDE_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)

    llm = _get_enhance_llm()

    assert llm.model_name == "qwen2.5:1.5b"
    assert llm.openai_api_base == "http://localhost:11434/v1"
    assert llm.openai_api_key.get_secret_value() == "ollama"
