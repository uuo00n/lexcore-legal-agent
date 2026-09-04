"""模型 / Provider 解析口径的回归锁。

历史事故：10 处节点各写一份 ``os.getenv("XXX_MODEL", "<视觉模型名>")``，
纯文本推理节点因此全部调用视觉模型，而且全局 ``LLM_MODEL`` 被节点自带的默认值盖掉。
这里锁住三件事：

1. 主链节点不得自己读模型 / Provider 环境变量，必须走 services.model_defaults；
2. 档位内置默认模型不得是视觉 / 多模态模型；
3. 覆盖优先级：节点专属 env → 档位 env → ``LLM_MODEL`` → 档位内置默认值。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from services import model_defaults
from services.model_defaults import (
    FAST,
    LONG,
    STRONG,
    TIER_DEFAULT_MODELS,
    VISION_MODEL_MARKERS,
    resolve_model,
    resolve_provider,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_VISION_MODEL = "deepseek-v4-flash-vision-exp"
# 只有 model_defaults 的模块文档可以提到这个模型名——它在那里是用来讲清这段历史的。
LITERAL_ALLOWLIST = {Path("services/model_defaults.py")}
MODEL_ENV_GETENV = re.compile(r"os\.getenv\(\s*[\"'][A-Z_]*(?:MODEL|PROVIDER)[\"']")


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch):
    """清掉本机 .env 注入的模型变量，并复位视觉模型告警去重集合。"""
    for name in list(os.environ):
        if name.endswith(("_MODEL", "_PROVIDER")):
            monkeypatch.delenv(name, raising=False)
    model_defaults._warned_vision_models.clear()
    yield
    model_defaults._warned_vision_models.clear()


def _python_files(*relative_dirs: str) -> list[Path]:
    files: list[Path] = []
    for relative in relative_dirs:
        for path in (REPO_ROOT / relative).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


def test_main_chain_nodes_do_not_read_model_env_directly():
    offenders = []
    targets = _python_files("agent")
    targets += [REPO_ROOT / "services" / "supervisor.py", REPO_ROOT / "services" / "model_routing.py"]
    for path in targets:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if MODEL_ENV_GETENV.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "节点不得自己读模型 / Provider 环境变量，请改用 services.model_defaults."
        f"resolve_model / resolve_provider：{offenders}"
    )


def test_legacy_vision_model_name_is_not_hardcoded_in_sources():
    offenders = []
    for path in _python_files("agent", "services", "api"):
        relative = path.relative_to(REPO_ROOT)
        if relative in LITERAL_ALLOWLIST:
            continue
        if LEGACY_VISION_MODEL in path.read_text(encoding="utf-8"):
            offenders.append(str(relative))
    assert not offenders, f"视觉模型名不得写进源码，只能由环境变量显式配置：{offenders}"


def test_tier_defaults_are_text_models():
    for tier, model in TIER_DEFAULT_MODELS.items():
        lowered = model.lower()
        hits = [marker for marker in VISION_MODEL_MARKERS if marker in lowered]
        assert not hits, f"档位 {tier} 的默认模型 {model} 命中视觉模型特征 {hits}"


def test_falls_back_to_tier_default_when_nothing_configured():
    assert resolve_model("SUPERVISOR_MODEL", tier=FAST) == TIER_DEFAULT_MODELS[FAST]
    assert resolve_model("VERIFIER_MODEL", tier=STRONG) == TIER_DEFAULT_MODELS[STRONG]
    assert resolve_model(tier=LONG) == TIER_DEFAULT_MODELS[LONG]


def test_global_llm_model_overrides_tier_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")

    assert resolve_model("SUPERVISOR_MODEL", tier=FAST) == "deepseek-v4-pro"


def test_tier_env_overrides_global_llm_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_ROUTE_FAST_MODEL", "deepseek-v4-flash")

    assert resolve_model("SUPERVISOR_MODEL", tier=FAST) == "deepseek-v4-flash"
    assert resolve_model("VERIFIER_MODEL", tier=STRONG) == "deepseek-v4-pro"


def test_node_env_wins_and_falls_through_the_chain(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "global-model")
    monkeypatch.setenv("SUPERVISOR_MODEL", "legacy-node-model")

    assert resolve_model("VERIFIER_MODEL", "SUPERVISOR_MODEL", tier=STRONG) == "legacy-node-model"

    monkeypatch.setenv("VERIFIER_MODEL", "node-model")
    assert resolve_model("VERIFIER_MODEL", "SUPERVISOR_MODEL", tier=STRONG) == "node-model"


def test_blank_env_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_MODEL", "   ")

    assert resolve_model("SUPERVISOR_MODEL", tier=FAST) == TIER_DEFAULT_MODELS[FAST]


def test_provider_defaults_to_none_so_llm_provider_decides(monkeypatch):
    assert resolve_provider("SUPERVISOR_PROVIDER", tier=FAST) is None

    monkeypatch.setenv("LLM_ROUTE_FAST_PROVIDER", "qwen")
    assert resolve_provider("SUPERVISOR_PROVIDER", tier=FAST) == "qwen"

    monkeypatch.setenv("SUPERVISOR_PROVIDER", "zhipu")
    assert resolve_provider("SUPERVISOR_PROVIDER", tier=FAST) == "zhipu"


def test_unknown_tier_fails_fast():
    with pytest.raises(ValueError):
        resolve_model("SUPERVISOR_MODEL", tier="turbo")


def test_vision_model_configuration_is_warned(monkeypatch, caplog):
    monkeypatch.setenv("SUPERVISOR_MODEL", LEGACY_VISION_MODEL)

    with caplog.at_level("WARNING", logger="services.model_defaults"):
        assert resolve_model("SUPERVISOR_MODEL", tier=FAST) == LEGACY_VISION_MODEL

    assert any(LEGACY_VISION_MODEL in record.getMessage() for record in caplog.records)
