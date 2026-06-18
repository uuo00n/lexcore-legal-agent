from __future__ import annotations

import json

import pytest

from scripts.render_openviking_config import build_openviking_config


def test_build_openviking_config_uses_glm47_and_full_semantic_layers():
    config = build_openviking_config({
        "ZHIPU_API_KEY": "test-key",
        "OPENVIKING_WORKSPACE": "/tmp/legal-openviking-data",
        "OPENVIKING_GLM_API_BASE": "https://open.bigmodel.cn/api/paas/v4",
        "OPENVIKING_GLM_MODEL": "glm-4.7",
    })

    dumped = json.dumps(config, ensure_ascii=False)

    assert config["vlm"]["provider"] == "openai"
    assert config["vlm"]["model"] == "glm-4.7"
    assert config["vlm"]["api_key"] == "test-key"
    assert config["vlm"]["api_base"] == "https://open.bigmodel.cn/api/paas/v4"
    assert config["vlm"]["max_concurrent"] == 1
    assert config["query_planner"]["model"] == "glm-4.7"
    assert config["query_planner"]["max_concurrent"] == 1
    assert config["auto_generate_l0"] is True
    assert config["auto_generate_l1"] is True
    assert config["default_search_mode"] == "thinking"
    assert config["embedding"]["dense"]["model"] == "bge-small-zh-v1.5"
    assert config["embedding"]["dense"]["dimension"] == 512
    assert "qwen" not in dumped.lower()
    assert "ollama" not in dumped.lower()


def test_build_openviking_config_requires_glm_api_key():
    with pytest.raises(RuntimeError, match="ZHIPU_API_KEY"):
        build_openviking_config({})
