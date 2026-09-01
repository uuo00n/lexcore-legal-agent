"""RAG 管道集成测试 —— 覆盖分块、检索、工具调用全链路。

测试分层：
- 单元测试：分块器、分词器
- 集成测试：语义检索、关键词检索、混合检索、搜索工具
- 端到端测试：完整 RAG 管线（需要已构建的向量索引）

运行方式：
    pytest tests/test_rag.py -v
    或跳过需要索引的测试：pytest tests/test_rag.py -v -k "not index"
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ─── 测试配置 ──────────────────────────────────────────────────────────────
LAWS_DIR = Path(__file__).resolve().parent.parent / "data" / "laws"
LABOR_LAW_FILE = "07_劳动法.txt"  # 用于分块测试


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def labor_law_path():
    """
    函数作用：
        劳动法文件路径 fixture。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    path = LAWS_DIR / LABOR_LAW_FILE
    if not path.exists():
        pytest.skip(f"法律文件 {LABOR_LAW_FILE} 不存在")
    return path


@pytest.fixture
def chunks():
    """
    函数作用：
        加载所有法律分块，用于检索测试。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from services.indexer.chunker import chunk_all_laws
    if not LAWS_DIR.exists():
        pytest.skip(f"法律目录 {LAWS_DIR} 不存在")
    return chunk_all_laws(LAWS_DIR)


@pytest.fixture
def semantic_retriever():
    """
    函数作用：
        创建语义检索器实例（仅用于集成测试，需已构建向量索引）。
    输入参数：
        - 无
    输出参数：
        - 未标注
    """
    from services.rag import get_vector_store
    from services.rag.retriever import SemanticRetriever
    try:
        store = get_vector_store()
        store.search([0.0] * 384, top_k=1)
    except Exception:
        pytest.skip("向量存储未初始化或无索引数据")
    return SemanticRetriever(store)


# ─── 分块器单元测试 ─────────────────────────────────────────────────────────

class TestChunker:
    """法律文本分块器单元测试。"""

    def test_chunk_law_file_returns_chunks(self, labor_law_path):
        """
        函数作用：
            分块劳动法文件，应返回非空 LawChunk 列表。
        输入参数：
            - labor_law_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file
        from services.rag.interfaces import LawChunk

        result = chunk_law_file(labor_law_path)

        assert len(result) > 0, "劳动法应生成至少一个 chunk"
        assert all(isinstance(c, LawChunk) for c in result), "每个元素都应是 LawChunk 实例"

    def test_chunk_law_name_extracted(self, labor_law_path):
        """
        函数作用：
            分块后的每个 chunk 应包含正确的法律名。
        输入参数：
            - labor_law_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file

        result = chunk_law_file(labor_law_path)

        for c in result:
            assert c.law_name == "劳动法", f"chunk {c.chunk_id} 法律名应为 '劳动法'，实际为 '{c.law_name}'"

    def test_chunk_article_no_filled(self, labor_law_path):
        """
        函数作用：
            每条 chunk 的 article_no 字段不应为空。
        输入参数：
            - labor_law_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file

        result = chunk_law_file(labor_law_path)

        for c in result:
            assert c.article_no, f"chunk {c.chunk_id} 缺少条款号"
            assert c.article_no.startswith("第"), \
                f"条款号应以 '第' 开头，实际为 '{c.article_no}'"

    def test_chunk_content_not_empty(self, labor_law_path):
        """
        函数作用：
            每条 chunk 的 content 不应为空。
        输入参数：
            - labor_law_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file

        result = chunk_law_file(labor_law_path)

        for c in result:
            assert c.content.strip(), f"chunk {c.chunk_id} 内容为空"
            assert len(c.content) > 10, \
                f"chunk {c.chunk_id} 内容太短（{len(c.content)} 字符），可能分块有误"

    def test_chunk_id_unique(self, labor_law_path):
        """
        函数作用：
            同一文件内的 chunk_id 应唯一。
        输入参数：
            - labor_law_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file

        result = chunk_law_file(labor_law_path)
        ids = [c.chunk_id for c in result]

        assert len(ids) == len(set(ids)), "chunk_id 存在重复"

    def test_chunk_all_laws(self, chunks):
        """
        函数作用：
            chunk_all_laws 应生成所有法律的 chunk 合集。
        输入参数：
            - chunks: 未标注
        输出参数：
            - 未标注
        """
        assert len(chunks) > 100, f"30 部法律至少应有 100+ 个 chunk，实际仅 {len(chunks)}"

    def test_known_article_present(self, labor_law_path):
        """
        函数作用：
            验证劳动法第 36 条（工时上限）存在且内容正确。
        输入参数：
            - labor_law_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file

        result = chunk_law_file(labor_law_path)
        article_36 = [c for c in result if c.article_no == "第三十六条"]

        assert len(article_36) == 1, f"第 36 条应恰好存在一条，实际 {len(article_36)} 条"
        assert "八小时" in article_36[0].content or "不超过" in article_36[0].content, \
            "第 36 条（工时上限）内容应包含 '八小时' 或 '不超过'"

    def test_chunk_contains_qdrant_legal_payload_metadata(self, labor_law_path):
        from services.indexer.chunker import chunk_law_file
        from services.rag.interfaces import LEGAL_PAYLOAD_FIELDS, document_payload

        chunk = chunk_law_file(labor_law_path)[0]
        payload = document_payload(chunk)

        assert set(LEGAL_PAYLOAD_FIELDS).issubset(payload)
        assert payload["document_id"] == chunk.chunk_id
        assert payload["law_name"] == "劳动法"
        assert payload["law_type"] == "法律"
        assert payload["status"]
        assert payload["publish_date"]
        assert payload["effective_date"]
        assert payload["source"]
        assert payload["source_path"].endswith("07_劳动法.txt")
        assert len(payload["content_hash"]) == 64

    def test_chunk_fallback_items_for_amendment_text(self, tmp_path):
        """
        函数作用：
            修正案等非“第X条”结构文本，应按“一、二、”分项生成可检索 chunk。
        输入参数：
            - tmp_path: 未标注
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_law_file

        law_path = tmp_path / "57_刑法修正案十二.txt"
        law_path.write_text(
            "\n".join([
                "# 中华人民共和国刑法修正案（十二）",
                "来源: 国家法律法规数据库 https://flk.npc.gov.cn/",
                "官方标题: 中华人民共和国刑法修正案（十二）",
                "更新时间: 2026-06-17",
                "",
                "中华人民共和国刑法修正案（十二）",
                "（2023年12月29日通过）",
                "一、在刑法第一百六十五条中增加一款。",
                "二、将刑法第一百六十六条修改。",
            ]),
            encoding="utf-8",
        )

        result = chunk_law_file(law_path)

        assert [chunk.article_no for chunk in result] == ["前言", "第一项", "第二项"]
        assert "官方标题" not in result[0].content
        assert "第一百六十五条" in result[1].content


# ─── 分词器单元测试 ─────────────────────────────────────────────────────────

class TestTokenization:
    """BM25 中文分词器单元测试。"""

    def test_tokenize_chinese(self):
        """
        函数作用：
            中文文本应被正确分词为 unigram + bigram。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.bm25 import _tokenize

        tokens = _tokenize("劳动合同")

        # 应有 unigram: '劳', '动', '合', '同' + bigram: '劳动', '动合', '合同'
        assert '劳' in tokens
        assert '劳动' in tokens
        assert '合同' in tokens

    def test_tokenize_mixed(self):
        """
        函数作用：
            中英混合文本应正确分词。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.bm25 import _tokenize

        tokens = _tokenize("BM25算法测试")

        assert 'bm25' in tokens  # 英文应转小写
        assert '算' in tokens
        assert '算法' in tokens

    def test_tokenize_empty(self):
        """
        函数作用：
            空文本应返回空列表。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.bm25 import _tokenize

        tokens = _tokenize("")
        assert tokens == []


# ─── 关键词检索集成测试 ─────────────────────────────────────────────────────

class TestKeywordRetriever:
    """BM25 关键词检索器集成测试。"""

    def test_build_and_retrieve(self, chunks):
        """
        函数作用：
            构建 BM25 索引并检索，应返回相关结果。
        输入参数：
            - chunks: 未标注
        输出参数：
            - 未标注
        """
        from services.rag.bm25 import BM25Retriever

        retriever = BM25Retriever(chunks)
        results = retriever.retrieve("加班费怎么算", top_k=5)

        assert len(results) > 0, "检索 '加班费怎么算' 应返回至少一条结果"
        assert len(results) <= 5, "结果数不应超过 top_k"

    def test_relevant_results_contain_keywords(self, chunks):
        """
        函数作用：
            检索 '试用期' 的结果应包含相关条款。
        输入参数：
            - chunks: 未标注
        输出参数：
            - 未标注
        """
        from services.rag.bm25 import BM25Retriever

        retriever = BM25Retriever(chunks)
        results = retriever.retrieve("试用期最长多久", top_k=10)

        # 至少有一条结果在内容或条款号中提到试用期
        has_relevant = any(
            "试用期" in chunk.content or "试用期" in chunk.article_no
            for chunk, _score in results
        )
        assert has_relevant, "试用期检索结果中应有包含 '试用期' 的条款"

    def test_empty_query_returns_empty(self, chunks):
        """
        函数作用：
            空查询应返回空结果（或被优雅处理）。
        输入参数：
            - chunks: 未标注
        输出参数：
            - 未标注
        """
        from services.rag.bm25 import BM25Retriever

        retriever = BM25Retriever(chunks)
        try:
            results = retriever.retrieve("", top_k=5)
            # 如果没有抛出异常，结果应为空
            assert isinstance(results, list)
        except ValueError:
            # 允许抛出 ValueError
            pass


# ─── 混合检索集成测试（需要向量索引）─────────────────────────────────────────

@pytest.mark.slow
class TestHybridRetriever:
    """混合检索器集成测试（需要已初始化的 RAG 系统）。"""

    def test_full_pipeline_returns_results(self):
        """
        函数作用：
            完整混合检索管线应返回结果。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.retriever import get_retriever

        try:
            retriever = get_retriever()
        except RuntimeError:
            pytest.skip("检索器未初始化，请先启动服务或运行 init")

        results = retriever.retrieve("劳动合同解除需要什么条件", top_k=5)

        assert len(results) > 0, "应返回至少一条结果"
        assert len(results) <= 5

    def test_results_have_required_fields(self):
        """
        函数作用：
            检索结果应包含 law_name、article_no、content 字段。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.retriever import get_retriever

        try:
            retriever = get_retriever()
        except RuntimeError:
            pytest.skip("检索器未初始化")

        results = retriever.retrieve("工伤认定", top_k=3)

        for chunk in results:
            assert chunk.law_name, f"缺少 law_name: {chunk.chunk_id}"
            assert chunk.article_no, f"缺少 article_no: {chunk.chunk_id}"
            assert chunk.content, f"缺少 content: {chunk.chunk_id}"

    def test_semantic_relevance(self):
        """
        函数作用：
            语义检索应返回与查询语义相关的结果。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.retriever import get_retriever

        try:
            retriever = get_retriever()
        except RuntimeError:
            pytest.skip("检索器未初始化")

        # 查询关于加班的问题，第一条结果应该与工时或加班相关
        results = retriever.retrieve("加班工资计算标准", top_k=5)

        if results:
            # 至少有一条来自劳动法或劳动合同法
            labor_laws = [c for c in results if "劳动" in c.law_name]
            assert len(labor_laws) > 0, \
                f"关于加班的查询应返回劳动法相关结果，实际法律分布: {[c.law_name for c in results]}"


# ─── 搜索工具集成测试 ──────────────────────────────────────────────────────

@pytest.mark.slow
class TestLegalSearchTool:
    """法律搜索工具集成测试。"""

    def test_search_returns_json_array(self):
        """
        函数作用：
            搜索工具应返回合法的 JSON 数组。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from agent.tools.rag_search import retrieve_local_law_tool
        try:
            result = retrieve_local_law_tool.invoke({"query": "试用期"})
        except RuntimeError:
            pytest.skip("检索器未初始化")

        data = json.loads(result)
        assert isinstance(data, dict), f"返回内容应为对象，实际为 {type(data)}"
        if data.get("results"):
            item = data["results"][0]
            assert "law_name" in item, "每条结果应包含 law_name"
            assert "content" in item, "每条结果应包含 content"

    def test_search_relevant_results(self):
        """
        函数作用：
            搜索 '试用期' 应返回劳动合同法相关条款。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from agent.tools.rag_search import retrieve_local_law_tool
        try:
            result = retrieve_local_law_tool.invoke({"query": "试用期最长多久"})
        except RuntimeError:
            pytest.skip("检索器未初始化")

        data = json.loads(result)
        if data.get("results"):
            has_trial_period = any(
                "试用" in item.get("content", "") for item in data["results"]
            )
            assert has_trial_period, \
                f"试用期检索结果应包含 '试用' 相关内容，实际: {[item.get('law_name', '') for item in data['results'][:3]]}"

    def test_search_empty_query_handled(self):
        """
        函数作用：
            空查询应被妥善处理（不崩溃）。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from agent.tools.rag_search import retrieve_local_law_tool
        try:
            result = retrieve_local_law_tool.invoke({"query": ""})
            assert isinstance(result, str)
        except RuntimeError:
            pytest.skip("检索器未初始化")


# ─── RRF 融合单元测试 ──────────────────────────────────────────────────────

class TestRRFFusion:
    """RRF 融合算法单元测试。"""

    def test_rrf_basic_fusion(self):
        """
        函数作用：
            两路结果经 RRF 融合后应保留共同出现的 chunk。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.interfaces import LawChunk
        from services.rag.retriever import HybridRetriever

        # 构造测试数据
        chunk_a = LawChunk(
            law_name="劳动法", hierarchy="", article_no="第一条",
            content="测试内容A", chunk_id="labor_第一条"
        )
        chunk_b = LawChunk(
            law_name="劳动合同法", hierarchy="", article_no="第二条",
            content="测试内容B", chunk_id="contract_第二条"
        )

        # 两路检索结果：A 在语义路排名高，B 在关键词路排名高
        semantic = [(chunk_a, 0.9), (chunk_b, 0.3)]
        keyword = [(chunk_b, 8.5), (chunk_a, 2.0)]

        retriever = HybridRetriever(rrf_k=60)
        fused = retriever._rrf_fuse(semantic, keyword)

        assert len(fused) == 2, "RRF 融合后应保留两个 chunk"
        # 两个 chunk 在各自路中排名互补，RRF 后应都保留

    def test_rrf_with_k_param(self):
        """
        函数作用：
            不同 k 参数应影响 RRF 排名但不会丢失 chunk。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.rag.interfaces import LawChunk
        from services.rag.retriever import HybridRetriever

        chunk = LawChunk(
            law_name="劳动法", hierarchy="", article_no="第一条",
            content="测试", chunk_id="test_1"
        )

        # 单路结果
        semantic = [(chunk, 0.9)]
        keyword = [(chunk, 5.0)]

        for k in [1, 30, 60, 120]:
            retriever = HybridRetriever(rrf_k=k)
            fused = retriever._rrf_fuse(semantic, keyword)
            assert len(fused) == 1, f"k={k} 时 RRF 应保留唯一的 chunk"
            assert fused[0] is chunk


# ─── 端到端 RAG 管线测试 ────────────────────────────────────────────────────

@pytest.mark.slow
class TestEndToEndRAG:
    """端到端 RAG 管线测试（需要完整系统初始化）。"""

    def test_full_search_workflow(self):
        """
        函数作用：
            完整检索工作流：chunk → index → retrieve → format。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_all_laws
        from services.rag.bm25 import BM25Retriever

        if not LAWS_DIR.exists():
            pytest.skip(f"法律目录 {LAWS_DIR} 不存在")

        # 1. 分块
        chunks = chunk_all_laws(LAWS_DIR)
        assert len(chunks) > 0, "分块不应为空"

        # 2. 构建 BM25 索引
        kw_retriever = BM25Retriever(chunks)

        # 3. 检索
        results = kw_retriever.retrieve("试用期最长多久", top_k=5)
        assert len(results) > 0, "BM25 检索应返回结果"

        # 4. 验证结果结构
        for chunk, score in results:
            assert chunk.law_name
            assert chunk.content
            assert isinstance(score, (int, float))
            assert score > 0, f"BM25 分数应 > 0，实际 {score}"

    def test_multiple_laws_indexed(self):
        """
        函数作用：
            验证多部法律都被正确分块。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_all_laws

        if not LAWS_DIR.exists():
            pytest.skip(f"法律目录 {LAWS_DIR} 不存在")

        chunks = chunk_all_laws(LAWS_DIR)
        law_names = {c.law_name for c in chunks}

        # 至少应有 20 部以上不同法律
        assert len(law_names) > 20, \
            f"应有 20+ 部法律，实际 {len(law_names)} 部: {sorted(law_names)}"

    def test_chunk_content_integrity(self):
        """
        函数作用：
            验证 chunk 内容完整性：content 应包含 article_no。
        输入参数：
            - 无
        输出参数：
            - 未标注
        """
        from services.indexer.chunker import chunk_all_laws

        if not LAWS_DIR.exists():
            pytest.skip(f"法律目录 {LAWS_DIR} 不存在")

        chunks = chunk_all_laws(LAWS_DIR)
        for chunk in chunks[:50]:  # 抽查前 50 条
            assert chunk.article_no in chunk.content, \
                f"chunk {chunk.chunk_id}: content 应包含条款号 '{chunk.article_no}'"
