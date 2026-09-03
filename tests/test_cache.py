from __future__ import annotations

from infrastructure.redis import RedisSettings, init_redis, reset_for_tests as reset_redis
from services.cache import get_cached_answer, make_cache_key, set_cached_answer


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        assert ex and ex > 0
        self.values[key] = value
        return True


def setup_function():
    reset_redis()
    init_redis(
        RedisSettings(url="redis://test", enabled=True),
        sync_client=_FakeRedis(),
    )


def teardown_function():
    reset_redis()


def test_response_cache_roundtrip():
    set_cached_answer(" 房东   不退押金 ", "请补充租赁合同情况", doc_id="doc-1")

    assert get_cached_answer("房东 不退押金", doc_id="doc-1") == "请补充租赁合同情况"
    assert get_cached_answer("房东 不退押金", doc_id="doc-2") is None


def test_cache_key_is_stable_for_whitespace():
    assert make_cache_key("公司  拖欠 工资") == make_cache_key("公司 拖欠 工资")
