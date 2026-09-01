"""Redis 缓存层测试：降级、key 脱敏、TTL、cache_hit trace。

覆盖第十九阶段五个要求：
1. Redis 挂掉时主链降级运行 —— 每个模块都有 *_degrades_* 用例
2. cache key 不存敏感数据 —— *_key_* 用例断言原文不出现在 key 中
3. 设置 TTL —— 断言 FakeRedis 记录到过期时间
4. 不长期缓存敏感合同文本 —— 响应缓存的 doc TTL 用例
5. trace 中记录 cache_hit —— *_records_trace_* 用例
"""
from __future__ import annotations

import json

import pytest

from infrastructure import redis as redis_infra
from services.cache import delilegal as delilegal_cache
from services.cache import idempotency, rate_limit, retrieval, session
from services.rag.interfaces import DocumentResult, LawChunk


class FakeRedisStore:
    """最小 Redis 替身：只实现被用到的命令，并记录每个 key 的 TTL。"""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}
        self.eval_calls = 0

    def get(self, key):
        return self.strings.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    def incr(self, key):
        value = int(self.strings.get(key, 0)) + 1
        self.strings[key] = str(value)
        return value

    def expire(self, key, seconds):
        if key not in self.strings and key not in self.hashes:
            return False
        self.expirations[key] = int(seconds)
        return True

    def ttl(self, key):
        return self.expirations.get(key, -1)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(self.strings.pop(key, None) is not None)
            removed += int(self.hashes.pop(key, None) is not None)
            self.expirations.pop(key, None)
        return removed

    def hset(self, key, mapping=None):
        bucket = self.hashes.setdefault(key, {})
        bucket.update({str(k): str(v) for k, v in (mapping or {}).items()})
        return len(mapping or {})

    def hincrby(self, key, field, amount=1):
        bucket = self.hashes.setdefault(key, {})
        value = int(bucket.get(field, 0)) + int(amount)
        bucket[field] = str(value)
        return value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def ping(self):
        return True

    def eval(self, _script, numkeys, key, window):
        assert numkeys == 1
        self.eval_calls += 1
        count = self.incr(key)
        if count == 1:
            self.expire(key, window)
        return [count, self.ttl(key)]


class BrokenRedisStore(FakeRedisStore):
    """所有命令都抛连接错误，用于验证降级路径。"""

    def _fail(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    get = set = incr = expire = ttl = delete = eval = _fail
    hset = hincrby = hgetall = ping = _fail


class FakeAsyncPipeline:
    """把命令排队后一次性执行，语义足够覆盖 session 模块的用法。"""

    def __init__(self, store: FakeRedisStore) -> None:
        self._store = store
        self._queue: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _queue(*args, **kwargs):
            self._queue.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self):
        return [getattr(self._store, name)(*args, **kwargs) for name, args, kwargs in self._queue]


class FakeAsyncRedis:
    """把同步替身包装成 await 接口。"""

    def __init__(self, store: FakeRedisStore) -> None:
        self._store = store

    def pipeline(self):
        return FakeAsyncPipeline(self._store)

    def __getattr__(self, name):
        attr = getattr(self._store, name)

        async def _call(*args, **kwargs):
            return attr(*args, **kwargs)

        return _call


def _install(store: FakeRedisStore) -> FakeRedisStore:
    """把替身装进 infrastructure.redis，跳过真实连接。"""
    redis_infra.reset_for_tests()
    redis_infra.init_redis(
        redis_infra.RedisSettings(url="redis://localhost:6379/0", enabled=True),
        client=FakeAsyncRedis(store),
        sync_client=store,
    )
    return store


@pytest.fixture
def store():
    yield _install(FakeRedisStore())
    redis_infra.reset_for_tests()


@pytest.fixture
def broken():
    yield _install(BrokenRedisStore())
    redis_infra.reset_for_tests()


@pytest.fixture
def events(monkeypatch):
    """捕获 record_event 调用，避免测试依赖 SQLite trace 表。"""
    captured: list[tuple[str, str, str, dict]] = []

    def _record(trace_id, event_type, *, name="", payload=None):
        captured.append((trace_id, event_type, name, payload or {}))

    monkeypatch.setattr("services.observability.record_event", _record)
    return captured


def _hits(count: int = 2) -> list[DocumentResult]:
    """构造若干条召回结果。"""
    return [
        DocumentResult(
            LawChunk(
                law_name="中华人民共和国民法典",
                hierarchy="第三编 合同",
                article_no=f"第{index}条",
                content=f"法条正文 {index}",
                chunk_id=f"chunk-{index}",
                metadata={"law_type": "法律"},
            ),
            1.0 - index * 0.1,
        )
        for index in range(1, count + 1)
    ]


# --- 检索缓存 -----------------------------------------------------------------


def test_retrieval_cache_roundtrip_and_ttl(store):
    params = {"final_top_k": 5}
    results = _hits()

    retrieval.set_cached_results("公司拖欠工资怎么办", results, reranked=True, params=params)
    cached = retrieval.get_cached_results("公司拖欠工资怎么办", params)

    assert cached is not None
    loaded, reranked = cached
    assert reranked is True
    assert [item.document.chunk_id for item in loaded] == ["chunk-1", "chunk-2"]
    assert [item.document.content for item in loaded] == ["法条正文 1", "法条正文 2"]
    assert loaded[0].document.metadata == {"law_type": "法律"}
    assert loaded[0].score == pytest.approx(0.9)

    key = retrieval.build_key("公司拖欠工资怎么办", params)
    assert store.expirations[key] == retrieval.ttl_seconds()


def test_retrieval_cache_key_excludes_raw_query(store):
    query = "我在朝阳区被公司拖欠了三个月工资"
    key = retrieval.build_key(query, {"final_top_k": 5})

    assert query not in key
    assert "朝阳区" not in key
    assert key.startswith("legal:cache:retrieval:")
    # 空白归一化后视为同一个问题，不同问题必须落到不同 key。
    assert retrieval.build_key("公司  拖欠 工资", {}) == retrieval.build_key("公司 拖欠 工资", {})
    assert retrieval.build_key("公司拖欠工资", {}) != retrieval.build_key("房东不退押金", {})
    # 检索参数变化不复用旧结果。
    assert retrieval.build_key(query, {"final_top_k": 5}) != retrieval.build_key(
        query, {"final_top_k": 10}
    )


def test_retrieval_cache_does_not_store_query_text(store):
    query = "我在朝阳区被公司拖欠了三个月工资"
    retrieval.set_cached_results(query, _hits(1), reranked=False, params={})

    assert all(query not in value for value in store.strings.values())


def test_retrieval_cache_records_trace_hit_and_miss(store, events):
    params = {"final_top_k": 5}

    assert retrieval.get_cached_results("离婚财产怎么分", params, trace_id="trace-1") is None
    retrieval.set_cached_results("离婚财产怎么分", _hits(1), reranked=True, params=params)
    assert retrieval.get_cached_results("离婚财产怎么分", params, trace_id="trace-1") is not None

    kinds = [event_type for _trace, event_type, _name, _payload in events]
    assert kinds == ["cache_miss", "cache_hit"]
    payload = events[-1][3]
    assert payload["namespace"] == "cache:retrieval"
    assert payload["degraded"] is False
    assert payload["result_count"] == 1
    assert "离婚财产怎么分" not in json.dumps(payload, ensure_ascii=False)


def test_retrieval_cache_degrades_when_redis_down(broken, events):
    # 读写都不得抛异常，调用方照常执行真实检索。
    retrieval.set_cached_results("公司拖欠工资", _hits(1), reranked=True, params={})
    assert retrieval.get_cached_results("公司拖欠工资", {}, trace_id="trace-1") is None

    assert events[-1][1] == "cache_miss"
    assert events[-1][3]["degraded"] is True


def test_retrieval_cache_empty_results_are_not_cached(store):
    retrieval.set_cached_results("尚未建索引的问题", [], reranked=False, params={})

    assert store.strings == {}


def test_retriever_pipeline_uses_cache(store, monkeypatch):
    """HybridRetriever 第二次调用应命中缓存，不再执行召回管线。"""
    from services.rag.retriever import HybridRetriever

    retriever = HybridRetriever(reranker=None)
    calls: list[str] = []

    def _fake_pipeline(query, *, top_k=None, trace_id=None):
        calls.append(query)
        return _hits(2), True

    monkeypatch.setattr(retriever, "_run_pipeline", _fake_pipeline)

    first = retriever.retrieve_with_scores("公司拖欠工资怎么办", top_k=2)
    second = retriever.retrieve_with_scores("公司拖欠工资怎么办", top_k=2)

    assert calls == ["公司拖欠工资怎么办"]
    assert [item.document.chunk_id for item in first] == [
        item.document.chunk_id for item in second
    ]


# --- 得理 API 响应缓存 ---------------------------------------------------------


async def test_delilegal_cache_roundtrip_and_ttl(store):
    payload = {"query": "劳动合同解除", "pageSize": 10}
    response = {"data": {"totalCount": 1, "list": [{"lawName": "劳动合同法"}]}}

    await delilegal_cache.set_cached_response("law_search", payload, response)
    cached = await delilegal_cache.get_cached_response("law_search", payload)

    assert cached == response
    key = delilegal_cache.build_key("law_search", payload)
    assert store.expirations[key] == delilegal_cache.ttl_seconds()


async def test_delilegal_cache_key_excludes_query_text(store):
    payload = {"query": "王某与李某离婚纠纷"}
    key = delilegal_cache.build_key("case_search", payload)

    assert "王某" not in key
    assert key.startswith("legal:cache:delilegal:case_search:")
    # 请求体字段顺序不同但内容相同 → 同一个 key。
    assert delilegal_cache.build_key("law_search", {"a": 1, "b": 2}) == delilegal_cache.build_key(
        "law_search", {"b": 2, "a": 1}
    )
    assert delilegal_cache.build_key("law_search", payload) != delilegal_cache.build_key(
        "case_search", payload
    )


async def test_delilegal_cache_records_trace_and_degrades(broken, events):
    payload = {"query": "劳动合同解除"}

    await delilegal_cache.set_cached_response("law_search", payload, {"data": {}})
    assert await delilegal_cache.get_cached_response(
        "law_search", payload, trace_id="trace-1"
    ) is None

    assert events[-1][1] == "cache_miss"
    assert events[-1][3]["degraded"] is True
    assert events[-1][3]["endpoint_type"] == "law_search"


async def test_delilegal_client_second_call_hits_cache(store):
    """带缓存后同一检索条件只应打一次上游。"""
    import httpx

    from services.delilegal.client import DelilegalClient
    from services.delilegal.config import DelilegalSettings
    from services.delilegal.schemas import LawSearchInput

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"data": {"totalCount": 0, "list": []}})

    settings = DelilegalSettings(
        base_url="https://openapi.delilegal.test",
        app_id="test-app",
        secret="test-secret",
        law_search_path="/law-search",
    )
    http_client = httpx.AsyncClient(
        base_url="https://openapi.delilegal.test", transport=httpx.MockTransport(handler)
    )
    client = DelilegalClient(settings, http_client=http_client, trace_id="trace-1")
    await client.search_laws(LawSearchInput(query="劳动合同解除"))
    await client.search_laws(LawSearchInput(query="劳动合同解除"))
    await http_client.aclose()

    assert requests == ["/law-search"]


# --- 限流 ---------------------------------------------------------------------


async def test_rate_limit_blocks_after_limit_and_sets_ttl(store):
    decisions = [
        await rate_limit.check_rate_limit("thread-1", limit=2, window_seconds=60)
        for _ in range(3)
    ]

    assert [d.allowed for d in decisions] == [True, True, False]
    assert decisions[0].remaining == 1
    assert decisions[-1].retry_after == 60
    assert "请求过于频繁" in decisions[-1].reason

    key = rate_limit.build_key("thread-1", "chat", 60)
    assert store.expirations[key] == 60
    # 每次判定只执行一个 Redis 端原子脚本，不存在 INCR 后漏设 TTL 的窗口。
    assert store.eval_calls == 3


async def test_rate_limit_key_excludes_thread_id(store):
    key = rate_limit.build_key("thread-张三-2026", "chat", 60)

    assert "张三" not in key
    assert "thread-张三-2026" not in key
    assert key.startswith("legal:ratelimit:chat:60:")


async def test_rate_limit_fails_open_when_redis_down(broken):
    decision = await rate_limit.check_rate_limit("thread-1", limit=1, window_seconds=60)

    assert decision.allowed is True
    assert decision.degraded is True
    assert decision.reason == ""


async def test_rate_limit_isolates_subjects_and_scopes(store):
    first = await rate_limit.check_rate_limit("thread-1", limit=1, window_seconds=60)
    other_subject = await rate_limit.check_rate_limit("thread-2", limit=1, window_seconds=60)
    other_scope = await rate_limit.check_rate_limit(
        "thread-1", scope="upload", limit=1, window_seconds=60
    )

    assert [first.allowed, other_subject.allowed, other_scope.allowed] == [True, True, True]


async def test_rate_limit_disabled_by_env(store, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

    for _ in range(5):
        assert (await rate_limit.check_rate_limit("thread-1", limit=1)).allowed is True
    assert store.strings == {}


# --- 会话元数据 ---------------------------------------------------------------


async def test_session_metadata_roundtrip_and_ttl(store):
    await session.touch_session("thread-1", trace_id="trace-1", has_document=True)
    await session.touch_session("thread-1", trace_id="trace-2", has_document=False)

    data = await session.get_session_metadata("thread-1")

    assert data["request_count"] == 2
    assert data["last_trace_id"] == "trace-2"
    assert data["has_document"] is False
    assert isinstance(data["last_active_at"], int)

    key = session.build_key("thread-1")
    assert store.expirations[key] == session.ttl_seconds()


async def test_session_metadata_rejects_content_fields(store):
    await session.touch_session(
        "thread-1",
        trace_id="trace-1",
        extra={"question": "我在朝阳区被拖欠工资", "title": "劳动纠纷", "provider": "deepseek"},
    )

    stored = store.hashes[session.build_key("thread-1")]

    assert "question" not in stored
    assert "title" not in stored
    assert stored["provider"] == "deepseek"
    assert "朝阳区" not in json.dumps(stored, ensure_ascii=False)
    assert set(stored) <= session.ALLOWED_FIELDS


async def test_session_metadata_key_excludes_thread_id(store):
    key = session.build_key("thread-张三")

    assert "张三" not in key
    assert key.startswith("legal:session:")


async def test_session_metadata_degrades_when_redis_down(broken):
    await session.touch_session("thread-1", trace_id="trace-1")

    assert await session.get_session_metadata("thread-1") == {}


async def test_session_metadata_cleared(store):
    await session.touch_session("thread-1", trace_id="trace-1")
    await session.clear_session_metadata("thread-1")

    assert await session.get_session_metadata("thread-1") == {}


# --- 幂等 ---------------------------------------------------------------------


async def test_idempotency_claim_then_duplicate_and_ttl(store):
    first = await idempotency.claim("chat", "req-1", ref="trace-1")
    second = await idempotency.claim("chat", "req-1")

    assert first.acquired is True
    assert second.acquired is False
    assert second.duplicate is True
    assert second.record["state"] == idempotency.STATE_IN_PROGRESS
    assert second.record["ref"] == "trace-1"

    key = idempotency.build_key("chat", "req-1")
    assert store.expirations[key] == idempotency.ttl_seconds()


async def test_idempotency_mark_completed_visible_to_retry(store):
    await idempotency.claim("chat", "req-1")
    await idempotency.mark_completed("chat", "req-1", ref="trace-1")

    retry = await idempotency.claim("chat", "req-1")

    assert retry.duplicate is True
    assert retry.completed is True
    record = await idempotency.get_record("chat", "req-1")
    assert record["state"] == idempotency.STATE_COMPLETED


async def test_idempotency_release_allows_retry(store):
    await idempotency.claim("chat", "req-1")
    await idempotency.release("chat", "req-1")

    assert (await idempotency.claim("chat", "req-1")).acquired is True


async def test_idempotency_degrades_to_allow(broken):
    claim = await idempotency.claim("chat", "req-1")

    assert claim.acquired is True
    assert claim.degraded is True
    assert claim.duplicate is False


async def test_idempotency_without_token_is_noop(store):
    claim = await idempotency.claim("chat", "")

    assert claim.acquired is True
    assert claim.duplicate is False
    assert store.strings == {}


async def test_idempotency_key_excludes_token(store):
    key = idempotency.build_key("chat", "user-张三-request-42")

    assert "张三" not in key
    assert "request-42" not in key
    assert key.startswith("legal:idempotency:chat:")


# --- infrastructure/redis.py --------------------------------------------------


def test_settings_without_url_disable_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_ENABLED", raising=False)

    settings = redis_infra.RedisSettings.from_env({})

    assert settings.enabled is False
    assert settings.key_prefix == redis_infra.DEFAULT_KEY_PREFIX


def test_settings_hide_password_in_safe_url():
    settings = redis_infra.RedisSettings(url="redis://:s3cr3t@cache.internal:6379/0")

    assert "s3cr3t" not in settings.safe_url
    assert "cache.internal" in settings.safe_url


def test_status_without_client_is_disabled():
    redis_infra.reset_for_tests()
    try:
        assert redis_infra.redis_status()["state"] in {"disabled", "uninitialized"}
        assert redis_infra.get_redis() is None
    finally:
        redis_infra.reset_for_tests()


async def test_ping_and_status_ok(store):
    assert await redis_infra.ping() is True
    assert redis_infra.redis_status()["state"] == "ok"


async def test_failure_opens_breaker_and_status_degrades(broken):
    assert await redis_infra.ping() is False
    assert redis_infra.breaker_open() is True
    assert redis_infra.redis_status()["state"] == "degraded"
    assert redis_infra.redis_status()["last_error"] == "ConnectionError"
    # 熔断打开期间不再返回客户端，调用方直接走降级分支。
    assert redis_infra.get_redis() is None
    assert redis_infra.get_sync_redis() is None


async def test_execute_returns_default_without_client():
    redis_infra.reset_for_tests()
    try:
        assert await redis_infra.execute("noop", lambda c: c.get("x"), default="fallback") == (
            "fallback"
        )
        assert redis_infra.execute_sync("noop", lambda c: c.get("x"), default=7) == 7
    finally:
        redis_infra.reset_for_tests()


def test_make_key_uses_prefix_and_skips_empty_parts(store):
    assert redis_infra.make_key("cache:demo", "a", "", "b") == "legal:cache:demo:a:b"
