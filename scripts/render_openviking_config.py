"""Render an OpenViking config for the legal assistant runtime.

The generated config intentionally uses GLM-4.7 for OpenViking semantic
processing and keeps local BGE embeddings behind an OpenAI-compatible endpoint.
Secrets are read from environment variables and written only to the runtime
config file, which should stay outside git.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


DEFAULT_OUTPUT = ".runtime/openviking/ov_glm47.conf"


def _env(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return value if value not in (None, "") else default


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = env.get(key)
    return int(value) if value not in (None, "") else default


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = env.get(key)
    return float(value) if value not in (None, "") else default


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env.get(key)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _glm_api_key(env: Mapping[str, str]) -> str:
    api_key = env.get("OPENVIKING_GLM_API_KEY") or env.get("ZHIPU_API_KEY")
    if not api_key:
        raise RuntimeError(
            "missing GLM API key: set OPENVIKING_GLM_API_KEY or ZHIPU_API_KEY"
        )
    return api_key


def build_openviking_config(env: Mapping[str, str] | None = None) -> dict:
    """Build an OpenViking ov.conf dictionary for full L0/L1/L2 processing."""
    env = env or os.environ
    workspace = _env(env, "OPENVIKING_WORKSPACE", ".runtime/openviking/workspace")
    glm_api_key = _glm_api_key(env)
    glm_api_base = _env(
        env,
        "OPENVIKING_GLM_API_BASE",
        "https://open.bigmodel.cn/api/paas/v4",
    )
    glm_model = _env(env, "OPENVIKING_GLM_MODEL", "glm-4.7")
    glm_provider = _env(env, "OPENVIKING_GLM_PROVIDER", "openai")
    glm_timeout = _env_float(env, "OPENVIKING_GLM_TIMEOUT", 120.0)
    glm_max_concurrent = _env_int(env, "OPENVIKING_GLM_MAX_CONCURRENT", 1)

    vlm = {
        "provider": glm_provider,
        "model": glm_model,
        "api_key": glm_api_key,
        "api_base": glm_api_base,
        "temperature": 0.0,
        "max_retries": _env_int(env, "OPENVIKING_GLM_MAX_RETRIES", 2),
        "timeout": glm_timeout,
        "max_concurrent": glm_max_concurrent,
        "stream": False,
    }

    query_planner = dict(vlm)
    query_planner["max_concurrent"] = _env_int(
        env,
        "OPENVIKING_QUERY_PLANNER_MAX_CONCURRENT",
        1,
    )

    return {
        "default_account": _env(env, "OPENVIKING_ACCOUNT", "default"),
        "default_user": _env(env, "OPENVIKING_USER", "default"),
        "default_agent": _env(env, "OPENVIKING_AGENT", "legal-assistant"),
        "storage": {
            "workspace": workspace,
        },
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": _env(env, "OPENVIKING_EMBEDDING_MODEL", "bge-small-zh-v1.5"),
                "api_key": _env(env, "OPENVIKING_EMBEDDING_API_KEY", "no-key"),
                "api_base": _env(
                    env,
                    "OPENVIKING_EMBEDDING_API_BASE",
                    "http://localhost:11435/v1",
                ),
                "dimension": _env_int(env, "OPENVIKING_EMBEDDING_DIMENSION", 512),
                "input": "text",
                "batch_size": _env_int(env, "OPENVIKING_EMBEDDING_BATCH_SIZE", 16),
            },
        },
        "vlm": vlm,
        "query_planner": query_planner,
        "server": {
            "host": _env(env, "OPENVIKING_SERVER_HOST", "127.0.0.1"),
            "port": _env_int(env, "OPENVIKING_SERVER_PORT", 1933),
            "workers": _env_int(env, "OPENVIKING_SERVER_WORKERS", 1),
            "auth_mode": _env(env, "OPENVIKING_AUTH_MODE", "dev"),
        },
        "auto_generate_l0": _env_bool(env, "OPENVIKING_AUTO_GENERATE_L0", True),
        "auto_generate_l1": _env_bool(env, "OPENVIKING_AUTO_GENERATE_L1", True),
        "default_search_mode": _env(env, "OPENVIKING_DEFAULT_SEARCH_MODE", "thinking"),
        "default_search_limit": _env_int(env, "OPENVIKING_DEFAULT_SEARCH_LIMIT", 5),
        "output_language_override": _env(env, "OPENVIKING_OUTPUT_LANGUAGE", "zh-CN"),
    }


def write_openviking_config(path: str | Path = DEFAULT_OUTPUT, env: Mapping[str, str] | None = None) -> Path:
    """Render and write the OpenViking config, returning the absolute path."""
    output = Path(path)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.parent.mkdir(parents=True, exist_ok=True)
    config = build_openviking_config(env)
    output.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def _redacted_summary(config: dict) -> dict:
    redacted = json.loads(json.dumps(config, ensure_ascii=False))
    for section in ("vlm", "query_planner"):
        if redacted.get(section, {}).get("api_key"):
            redacted[section]["api_key"] = "***"
    return redacted


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="生成 GLM-4.7 OpenViking ov.conf")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出配置路径")
    parser.add_argument(
        "--show",
        action="store_true",
        help="打印脱敏后的配置摘要",
    )
    args = parser.parse_args()

    path = write_openviking_config(args.output)
    print(f"OpenViking config written: {path}")
    if args.show:
        print(json.dumps(_redacted_summary(build_openviking_config()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
