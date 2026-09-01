import json

import httpx
import pytest

from services.delilegal.client import DelilegalClient
from services.delilegal.config import DelilegalSettings
from services.delilegal.exceptions import (
    DelilegalAuthenticationError,
    DelilegalConfigurationError,
    DelilegalTimeoutError,
    DelilegalUpstreamError,
)
from services.delilegal.schemas import CaseSearchInput, LawSearchInput
from services.retry import RetryPolicy, retry_async


def _settings(**overrides):
    values = {
        "base_url": "https://platform.delilegal.test",
        "api_key": "sk-test-api-key",
        "law_search_path": "/law-search",
        "case_search_path": "/api/v1/generice/case/list",
    }
    values.update(overrides)
    return DelilegalSettings(**values)


def _disable_retry_wait(monkeypatch):
    async def fast_retry(operation, *, operation_name):
        return await retry_async(
            operation,
            operation_name=operation_name,
            policy=RetryPolicy(max_attempts=3, multiplier=0, min_wait=0, max_wait=0),
        )

    monkeypatch.setattr("services.delilegal.client.retry_async", fast_retry)


async def test_client_posts_case_request_and_normalizes_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer sk-test-api-key"
        assert "appid" not in request.headers
        assert "secret" not in request.headers
        return httpx.Response(
            200,
            json={"success": True, "code": 0, "msg": "", "body": {"data": []}},
        )

    http_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test", transport=httpx.MockTransport(handler)
    )
    client = DelilegalClient(_settings(), http_client=http_client, trace_id="trace-1")
    response = await client.search_cases(CaseSearchInput(keywords=["劳动争议"]))
    await http_client.aclose()

    assert seen["path"] == "/api/v1/generice/case/list"
    assert seen["body"] == {
        "query": "劳动争议",
        "pageNo": 1,
        "pageSize": 5,
        "sortField": "correlation",
        "sortOrder": "desc",
    }
    assert response.items == []


async def test_law_path_must_be_configured():
    http_client = httpx.AsyncClient(base_url="https://platform.delilegal.test")
    client = DelilegalClient(_settings(law_search_path=None), http_client=http_client)
    with pytest.raises(DelilegalConfigurationError, match="DELILEGAL_LAW_SEARCH_PATH"):
        await client.search_laws(LawSearchInput(query="劳动法"))
    await http_client.aclose()


async def test_law_search_uses_confirmed_endpoint_and_request_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"success": True, "code": 0, "msg": "", "body": {"data": []}},
        )

    http_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test", transport=httpx.MockTransport(handler)
    )
    settings = _settings(law_search_path="/api/v1/generice/law/list")
    client = DelilegalClient(settings, http_client=http_client)
    await client.search_laws(
        LawSearchInput(query="工伤认定", page_size=3)
    )
    await http_client.aclose()

    assert seen["path"] == "/api/v1/generice/law/list"
    assert seen["body"] == {
        "pageNo": 1,
        "pageSize": 3,
        "sortField": "correlation",
        "sortOrder": "desc",
        "query": "工伤认定",
    }


async def test_client_maps_authentication_and_timeout_without_api_key():
    auth_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(401)),
    )
    client = DelilegalClient(_settings(), http_client=auth_client)
    with pytest.raises(DelilegalAuthenticationError) as auth_error:
        await client.search_cases(CaseSearchInput(keywords=["案例"]))
    assert "sk-test-api-key" not in str(auth_error.value)
    await auth_client.aclose()

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    timeout_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test", transport=httpx.MockTransport(timeout)
    )
    client = DelilegalClient(_settings(), http_client=timeout_client)
    with pytest.raises(DelilegalTimeoutError):
        await client.search_cases(CaseSearchInput(keywords=["案例"]))
    await timeout_client.aclose()


async def test_client_maps_application_authentication_failure_without_api_key():
    http_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"success": False, "code": 401, "msg": "invalid token"},
            )
        ),
    )
    client = DelilegalClient(_settings(), http_client=http_client)

    with pytest.raises(DelilegalAuthenticationError) as error:
        await client.search_cases(CaseSearchInput(keywords=["案例"]))
    await http_client.aclose()

    assert error.value.status_code == 401
    assert "sk-test-api-key" not in str(error.value)


async def test_client_retries_5xx_up_to_three_attempts(monkeypatch):
    _disable_retry_wait(monkeypatch)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"success": True, "code": 0, "msg": "", "body": {"data": []}},
        )

    http_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test", transport=httpx.MockTransport(handler)
    )
    client = DelilegalClient(_settings(), http_client=http_client)

    response = await client.search_cases(CaseSearchInput(keywords=["案例"]))
    await http_client.aclose()

    assert response.items == []
    assert attempts == 3


async def test_client_does_not_retry_http_400(monkeypatch):
    _disable_retry_wait(monkeypatch)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400)

    http_client = httpx.AsyncClient(
        base_url="https://platform.delilegal.test", transport=httpx.MockTransport(handler)
    )
    client = DelilegalClient(_settings(), http_client=http_client)

    with pytest.raises(DelilegalUpstreamError) as error:
        await client.search_cases(CaseSearchInput(keywords=["案例"]))
    await http_client.aclose()

    assert error.value.retryable is False
    assert attempts == 1


def test_settings_read_connection_values_from_environment(monkeypatch):
    monkeypatch.setenv("DELILEGAL_BASE_URL", "https://environment.delilegal.test")
    monkeypatch.setenv("DELILEGAL_API_KEY", "sk-environment-api-key")
    monkeypatch.delenv("DELILEGAL_LAW_SEARCH_PATH", raising=False)
    monkeypatch.delenv("DELILEGAL_CASE_SEARCH_PATH", raising=False)

    settings = DelilegalSettings.from_env()

    assert settings.base_url == "https://environment.delilegal.test"
    assert settings.api_key == "sk-environment-api-key"
    assert settings.law_search_path == "/api/v1/generice/law/list"
    assert settings.case_search_path == "/api/v1/generice/case/list"


def test_client_requires_base_url_from_environment(monkeypatch):
    monkeypatch.delenv("DELILEGAL_BASE_URL", raising=False)

    with pytest.raises(DelilegalConfigurationError, match="DELILEGAL_BASE_URL"):
        DelilegalClient(DelilegalSettings.from_env())


def test_client_requires_api_key_before_sending_request(monkeypatch):
    monkeypatch.delenv("DELILEGAL_API_KEY", raising=False)
    settings = DelilegalSettings(
        base_url="https://platform.delilegal.test",
        api_key="",
    )

    with pytest.raises(DelilegalConfigurationError, match="DELILEGAL_API_KEY"):
        settings.validate_credentials()
