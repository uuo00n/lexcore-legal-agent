"""回答格式清理工具。"""
from __future__ import annotations

import re


_SOURCE_APPENDIX_RE = re.compile(r"\n*\s*(?:---\s*\n)?\s*【引用法条】[\s\S]*$", re.MULTILINE)


def strip_answer_markdown(content: str) -> str:
    """
    函数作用：
        将法律咨询回答中的 Markdown 展示符号清理为普通中文文本。
    输入参数：
        - content: str
    输出参数：
        - str
    """
    if not content:
        return content

    text = str(content).replace("\r\n", "\n").replace("\r", "\n")
    text = _SOURCE_APPENDIX_RE.sub("", text)
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_\n]+)__", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
