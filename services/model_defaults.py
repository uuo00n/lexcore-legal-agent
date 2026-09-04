"""主链节点的模型 / Provider 解析口径（全项目单一来源）。

背景：此前 10 处节点各写一份 ``os.getenv("XXX_MODEL", "<模型名>")``。
commit 2344d4d 迁移到 DeepSeek 时，把旧栈的 GLM-4.6V 直译成了
``deepseek-v4-flash-vision-exp``，纯文本推理节点因此全部落到视觉模型；
之后每拆出一个新节点，这行默认值又被复制一遍。再加上 services/llm.py
中节点显式传入的 ``model`` 优先级高于全局 ``LLM_MODEL``，全局覆盖对这些
节点完全失效——不配环境变量必然走视觉模型，配了也只能一个个配。

现在节点不再写模型名，只声明「读哪些环境变量 + 属于哪个档位」：

    节点专属 env → 档位 env（``LLM_ROUTE_{FAST,STRONG,LONG}_MODEL``）
                 → 全局 ``LLM_MODEL`` → 档位内置默认值（只写在本文件）

档位环境变量与 services/model_routing.py 共用同一组，fast/strong/long
的口径在项目里只有一处定义。视觉 / 多模态模型不再作为任何文本节点的默认
值，只能显式配置给图片、扫描件识别链路；文本节点解析到视觉模型时记一条
warning，便于尽早发现配错的环境变量。
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Final

log = logging.getLogger(__name__)

FAST: Final = "fast"
STRONG: Final = "strong"
LONG: Final = "long"

#: 档位内置兜底模型：环境变量全都没配时才会用到，模型名只允许写在这里。
TIER_DEFAULT_MODELS: Final[dict[str, str]] = {
    FAST: "deepseek-v4-flash",
    STRONG: "deepseek-v4-pro",
    LONG: "deepseek-v4-pro",
}

#: 视觉 / 多模态模型的名称特征；文本节点解析到这类模型基本等于配置错误。
VISION_MODEL_MARKERS: Final[tuple[str, ...]] = ("vision", "-vl", "vl-")

_warned_vision_models: set[str] = set()


def _first_env(names: Iterable[str]) -> str | None:
    """
    函数作用：
        按给定顺序返回第一个非空环境变量值。
    输入参数：
        - names: Iterable[str]
    输出参数：
        - str | None
    """
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _check_tier(tier: str) -> str:
    """
    函数作用：
        校验档位名称，避免档位写错后静默落到别的默认模型。
    输入参数：
        - tier: str
    输出参数：
        - str
    """
    name = (tier or "").lower()
    if name not in TIER_DEFAULT_MODELS:
        raise ValueError(
            f"unknown model tier: {tier!r}, expected one of {list(TIER_DEFAULT_MODELS)}"
        )
    return name


def _warn_if_vision(model: str, env_names: tuple[str, ...], tier: str) -> None:
    """
    函数作用：
        文本节点解析到视觉模型时告警一次，不阻断调用。
    输入参数：
        - model: str
        - env_names: tuple[str, ...]
        - tier: str
    输出参数：
        - 无
    """
    lowered = model.lower()
    if not any(marker in lowered for marker in VISION_MODEL_MARKERS):
        return
    sources = " / ".join(env_names) or f"LLM_ROUTE_{tier.upper()}_MODEL / LLM_MODEL"
    key = f"{sources}:{model}"
    if key in _warned_vision_models:
        return
    _warned_vision_models.add(key)
    log.warning(
        "文本节点解析到视觉模型 %s（来源环境变量：%s）。视觉模型只用于图片 / 扫描件识别，"
        "文本推理节点请改配普通对话模型。",
        model,
        sources,
    )


def resolve_model(*env_names: str, tier: str = STRONG) -> str:
    """
    函数作用：
        解析某个节点该用的模型名，节点代码不再自带模型名。
    输入参数：
        - *env_names: str，节点专属环境变量，按优先级从高到低排列
        - tier: str，档位（FAST / STRONG / LONG），默认值 STRONG
    输出参数：
        - str
    """
    name = _check_tier(tier)
    model = (
        _first_env(env_names)
        or _first_env((f"LLM_ROUTE_{name.upper()}_MODEL", "LLM_MODEL"))
        or TIER_DEFAULT_MODELS[name]
    )
    _warn_if_vision(model, env_names, name)
    return model


def resolve_provider(*env_names: str, tier: str = STRONG) -> str | None:
    """
    函数作用：
        解析某个节点该用的 provider；返回 None 表示交给 LLM_PROVIDER 决定。
    输入参数：
        - *env_names: str，节点专属环境变量，按优先级从高到低排列
        - tier: str，档位（FAST / STRONG / LONG），默认值 STRONG
    输出参数：
        - str | None
    """
    name = _check_tier(tier)
    return _first_env((*env_names, f"LLM_ROUTE_{name.upper()}_PROVIDER"))
