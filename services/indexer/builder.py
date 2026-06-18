"""索引构建流水线 —— 将法律文本转化为可检索的向量索引。

流程：扫描 data/laws/ → 分块 → embedding → 写入向量库
支持 CLI 调用：python -m services.indexer.builder [--rebuild]
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from services.indexer.chunker import chunk_all_laws
from services.vectorstore import get_vectorstore

load_dotenv()

log = logging.getLogger("legal.indexer")


def _get_embedding_model():
    """
    函数作用：
        加载 embedding 模型（延迟导入，避免启动时加载大模型）。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from sentence_transformers import SentenceTransformer

    model_name = os.getenv("EMBEDDING_MODEL", "models/bge-small-zh-v1.5")
    model_path = Path(model_name)
    if model_path.exists():
        model_name = str(model_path.resolve())
    log.info("加载 embedding 模型: %s", model_name)
    return SentenceTransformer(model_name)


def build_index(
    laws_dir: str | Path = "data/laws",
    rebuild: bool = False,
) -> int:
    """
    函数作用：
        构建法律向量索引。
    输入参数：
        - laws_dir: str | Path，默认值 'data/laws'
        - rebuild: bool，默认值 False
    输出参数：
        - int
    """
    store = get_vectorstore()

    # 检查是否需要构建
    if not rebuild and store.count() > 0:
        log.info("索引已存在（%d 条），跳过构建。使用 --rebuild 强制重建。", store.count())
        return store.count()

    if rebuild:
        log.info("清空已有索引，准备重建...")
        store.clear()

    # Step 1: 分块
    log.info("开始分块: %s", laws_dir)
    start = time.time()
    chunks = chunk_all_laws(laws_dir)
    log.info("分块完成: %d 个 chunk，耗时 %.1fs", len(chunks), time.time() - start)

    if not chunks:
        log.warning("未找到任何法条分块，请检查 %s 目录下是否有 .txt 文件", laws_dir)
        return 0

    # Step 2: Embedding
    model = _get_embedding_model()
    log.info("开始生成 embedding...")
    start = time.time()

    # 批量编码，显示进度
    texts = [c.content for c in chunks]
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,  # bge 模型推荐归一化
    ).tolist()

    log.info("Embedding 完成: %d 条，耗时 %.1fs", len(embeddings), time.time() - start)

    # Step 3: 写入向量库
    log.info("写入向量库...")
    start = time.time()
    store.add_chunks(chunks, embeddings)
    log.info("写入完成，耗时 %.1fs。索引总量: %d", time.time() - start, store.count())

    return store.count()


def load_or_build_index(laws_dir: str | Path = "data/laws") -> int:
    """
    函数作用：
        启动时调用：如果索引已存在则加载，否则自动构建。
    输入参数：
        - laws_dir: str | Path，默认值 'data/laws'
    输出参数：
        - int
    """
    return build_index(laws_dir=laws_dir, rebuild=False)


# CLI 入口
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="法律向量索引构建工具")
    parser.add_argument(
        "--rebuild", action="store_true", help="强制重建索引（清空后重新构建）"
    )
    parser.add_argument(
        "--laws-dir", default="data/laws", help="法律文本目录路径"
    )
    args = parser.parse_args()

    count = build_index(laws_dir=args.laws_dir, rebuild=args.rebuild)
    print(f"\n索引构建完成，共 {count} 个法条分块。")
