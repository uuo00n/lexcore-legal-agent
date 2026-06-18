from __future__ import annotations

from services.cache import get_cached_answer, init_cache_tables, make_cache_key, set_cached_answer
from services.checkpoint import init_meta_db, reset_for_tests


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_response_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    init_meta_db()
    init_cache_tables()

    set_cached_answer(" 房东   不退押金 ", "请补充租赁合同情况", doc_id="doc-1")

    assert get_cached_answer("房东 不退押金", doc_id="doc-1") == "请补充租赁合同情况"
    assert get_cached_answer("房东 不退押金", doc_id="doc-2") is None


def test_cache_key_is_stable_for_whitespace():
    assert make_cache_key("公司  拖欠 工资") == make_cache_key("公司 拖欠 工资")
