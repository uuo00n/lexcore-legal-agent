#!/usr/bin/env python3
"""Normalize common English punctuation in Chinese legal Markdown/text files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PLACEHOLDER = "\x00P{}\x00"


def is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"


def has_cjk_near(text: str, index: int, window: int = 3) -> bool:
    start = max(0, index - window)
    end = min(len(text), index + window + 1)
    return any(is_cjk(text[pos]) for pos in range(start, end) if pos != index)


def protect(text: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def save(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return PLACEHOLDER.format(len(protected) - 1)

    patterns = [
        r"```[\s\S]*?```",
        r"`[^`]+`",
        r"!\[[^\]]*\]\([^)]+\)",
        r"\[[^\]]+\]\([^)]+\)",
        r"https?://[^\s)）]+",
    ]
    for pattern in patterns:
        text = re.sub(pattern, save, text)
    return text, protected


def restore(text: str, protected: list[str]) -> str:
    for index, original in enumerate(protected):
        text = text.replace(PLACEHOLDER.format(index), original)
    return text


def normalize_quotes(text: str) -> str:
    result: list[str] = []
    double_open = True
    single_open = True
    for index, char in enumerate(text):
        if char == '"':
            result.append("“" if double_open else "”")
            double_open = not double_open
            continue
        if char == "'":
            prev_char = text[index - 1] if index else ""
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if prev_char.isalpha() and next_char.isalpha():
                result.append(char)
            else:
                result.append("‘" if single_open else "’")
                single_open = not single_open
            continue
        result.append(char)
    return "".join(result)


def normalize_punctuation(text: str) -> str:
    if not text:
        return text

    prefix = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            end += len("\n---")
            prefix = text[:end]
            body = text[end:]

    body, protected = protect(body)
    body = normalize_quotes(body)

    converted: list[str] = []
    for index, char in enumerate(body):
        prev_char = body[index - 1] if index else ""
        next_char = body[index + 1] if index + 1 < len(body) else ""
        near_cjk = has_cjk_near(body, index)

        if char == "," and near_cjk:
            converted.append("，")
        elif char == ";" and near_cjk:
            converted.append("；")
        elif char == "?" and near_cjk:
            converted.append("？")
        elif char == "!" and near_cjk:
            converted.append("！")
        elif char == ":" and near_cjk and not (prev_char.isdigit() and next_char.isdigit()):
            converted.append("：")
        elif char == "(" and has_cjk_near(body, index, window=2):
            converted.append("（")
        elif char == ")" and has_cjk_near(body, index, window=2):
            converted.append("）")
        elif char == "." and is_cjk(prev_char):
            converted.append("。")
        else:
            converted.append(char)

    return prefix + restore("".join(converted), protected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    output = normalize_punctuation(text)
    target = args.output or args.input
    target.write_text(output, encoding="utf-8")
    print(f"normalized: {target}")


if __name__ == "__main__":
    main()
