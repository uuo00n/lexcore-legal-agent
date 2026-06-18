"""Import local legal corpus and workflow skills into a real OpenViking server.

Usage:
    python scripts/import_openviking_corpus.py --laws --skills --wait
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from services.openviking_client import OpenVikingHTTPClient, OpenVikingSettings
from services.openviking_ingest import (
    import_law_article_resources,
    import_law_resources,
    import_legal_skills,
)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="导入法律语料和法律流程 Skill 到 OpenViking")
    parser.add_argument("--laws", action="store_true", help="导入 data/laws 下的法律全文 Resource")
    parser.add_argument(
        "--article-cards",
        action="store_true",
        help="按法条导入轻量 Resource 卡片，用于法条级检索和 rerank",
    )
    parser.add_argument("--skills", action="store_true", help="导入法律咨询流程 Skill")
    parser.add_argument("--laws-dir", default="data/laws", help="法律文本目录，默认 data/laws")
    parser.add_argument("--wait", action="store_true", help="等待 OpenViking 建索引完成")
    parser.add_argument(
        "--no-build-index",
        action="store_true",
        help="导入资源后不立即触发索引构建/重建",
    )
    parser.add_argument(
        "--domains",
        default="",
        help="只导入指定 domain，逗号分隔，例如 labor,consumer_protection",
    )
    parser.add_argument(
        "--max-articles-per-law",
        type=int,
        default=None,
        help="每部法律最多导入多少个法条卡片，默认不限制",
    )
    parser.add_argument(
        "--write-mode",
        default="create",
        choices=["create", "replace", "append", "upsert"],
        help="法条卡片写入模式，默认 create；upsert 会先 replace，不存在再 create",
    )
    parser.add_argument(
        "--reindex-mode",
        default="vectors_only",
        choices=["vectors_only", "semantic_and_vectors", "all"],
        help="法条卡片导入后的重建模式；完整 L0/L1 语义层使用 semantic_and_vectors，all 是兼容别名",
    )
    parser.add_argument(
        "--no-write-wait",
        action="store_true",
        help="写入每张法条卡片时不等待单条语义任务完成；适合 semantic_and_vectors 后统一等待",
    )
    parser.add_argument(
        "--wait-after-import",
        action="store_true",
        help="全部导入请求提交后，统一等待 OpenViking 后台队列处理完成",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=None,
        help="--wait-after-import 使用的等待秒数，默认使用 OPENVIKING_TIMEOUT",
    )
    args = parser.parse_args()

    if not args.laws and not args.article_cards and not args.skills:
        parser.error("至少指定 --laws、--article-cards 或 --skills")

    settings = OpenVikingSettings.from_env()
    client = OpenVikingHTTPClient(settings)
    include_domains = {item.strip() for item in args.domains.split(",") if item.strip()} or None

    results = {}
    try:
        if args.laws:
            results["laws"] = import_law_resources(
                client,
                args.laws_dir,
                wait=args.wait,
                build_index=not args.no_build_index,
                include_domains=include_domains,
            )
        if args.article_cards:
            results["article_cards"] = import_law_article_resources(
                client,
                args.laws_dir,
                wait=args.wait,
                build_index=not args.no_build_index,
                include_domains=include_domains,
                max_articles_per_law=args.max_articles_per_law,
                mode=args.write_mode,
                timeout=settings.timeout,
                reindex_mode=args.reindex_mode,
                write_wait=False if args.no_write_wait else None,
            )
        if args.skills:
            results["skills"] = import_legal_skills(client, wait=args.wait)
        if args.wait_after_import:
            results["wait_processed"] = client.wait_processed(
                timeout=args.wait_timeout if args.wait_timeout is not None else settings.timeout
            )
    finally:
        client.close()

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
