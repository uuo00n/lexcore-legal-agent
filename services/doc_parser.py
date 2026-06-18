"""文档解析：PDF / DOCX / TXT → 纯文本。"""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
import docx


SUPPORTED_EXTS = {".pdf", ".docx", ".txt"}
MAX_CHARS = 60_000


class UnsupportedDocumentError(ValueError):
    pass


def parse_document(path: str | Path) -> tuple[str, bool]:
    """
    函数作用：
        返回 (text, truncated)。
    输入参数：
        - path: str | Path
    输出参数：
        - tuple[str, bool]
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix not in SUPPORTED_EXTS:
        raise UnsupportedDocumentError(f"unsupported extension: {suffix}")

    if suffix == ".pdf":
        reader = PdfReader(str(p))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif suffix == ".docx":
        doc = docx.Document(str(p))
        text = "\n".join(para.text for para in doc.paragraphs)
    else:
        text = p.read_text(encoding="utf-8", errors="ignore")

    text = text.strip()
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS], True
    return text, False
