from __future__ import annotations

import httpx

from services.openviking_client import (
    OpenVikingHTTPClient,
    OpenVikingMatch,
    OpenVikingSettings,
)


def test_openviking_http_client_add_resource_uses_real_api_shape(tmp_path):
    uploaded = {}
    created = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openviking/api/v1/resources/temp_upload":
            uploaded["api_key"] = request.headers.get("X-API-Key")
            uploaded["content_type"] = request.headers.get("Content-Type")
            return httpx.Response(200, json={"result": {"temp_file_id": "tmp-001"}})
        if request.url.path == "/openviking/api/v1/resources":
            created["payload"] = request.read().decode("utf-8")
            return httpx.Response(200, json={"result": {"root_uri": "viking://resources/laws/labor"}})
        return httpx.Response(404)

    file_path = tmp_path / "labor.md"
    file_path.write_text("# 劳动法\n\n第一条 测试", encoding="utf-8")
    client = OpenVikingHTTPClient(
        OpenVikingSettings(base_url="http://ov.local/openviking", api_key="secret"),
        transport=httpx.MockTransport(handler),
    )

    result = client.add_resource(
        file_path,
        to="viking://resources/laws/labor/labor.md",
        reason="legal corpus",
        wait=True,
        build_index=True,
    )

    assert result["root_uri"] == "viking://resources/laws/labor"
    assert uploaded["api_key"] == "secret"
    assert "multipart/form-data" in uploaded["content_type"]
    assert '"temp_file_id":"tmp-001"' in created["payload"]
    assert '"to":"viking://resources/laws/labor/labor.md"' in created["payload"]
    assert '"wait":true' in created["payload"]
    assert '"create_parent":true' in created["payload"]


def test_openviking_http_client_health_uses_root_health_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        if request.url.path == "/openviking/health":
            return httpx.Response(200, json={"status": "ok", "healthy": True})
        return httpx.Response(404)

    client = OpenVikingHTTPClient(
        OpenVikingSettings(base_url="http://ov.local/openviking"),
        transport=httpx.MockTransport(handler),
    )

    assert client.health() == {"status": "ok", "healthy": True}
    assert seen["path"] == "/openviking/health"


def test_openviking_http_client_find_normalizes_result_items():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openviking/api/v1/search/find"
        assert request.headers.get("X-API-Key") == "secret"
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "result": {
                    "resources": [
                        {
                            "uri": "viking://resources/laws/labor/劳动合同法.md",
                            "score": 0.91,
                            "abstract": "劳动争议资源",
                            "content": "劳动合同法 第二十条",
                        }
                    ],
                    "skills": [
                        {
                            "uri": "viking://user/default/skills/labor-arbitration-workflow",
                            "score": 0.72,
                            "abstract": "劳动仲裁流程",
                        }
                    ],
                }
            },
        )

    client = OpenVikingHTTPClient(
        OpenVikingSettings(base_url="http://ov.local/openviking", api_key="secret"),
        transport=httpx.MockTransport(handler),
    )

    matches = client.find(
        "试用期工资",
        target_uri="viking://resources/laws/labor/",
        context_type="resource",
        limit=3,
    )

    assert '"filter":{"op":"must","field":"context_type","conds":["resource"]}' in captured["payload"]
    assert matches == [
        OpenVikingMatch(
            uri="viking://resources/laws/labor/劳动合同法.md",
            context_type="resource",
            score=0.91,
            abstract="劳动争议资源",
            content="劳动合同法 第二十条",
        ),
        OpenVikingMatch(
            uri="viking://user/default/skills/labor-arbitration-workflow",
            context_type="skill",
            score=0.72,
            abstract="劳动仲裁流程",
            content="",
        ),
    ]


def test_openviking_http_client_reindex_and_wait_processed_use_real_api_shape():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.read().decode("utf-8")))
        if request.url.path == "/openviking/api/v1/content/reindex":
            return httpx.Response(200, json={"result": {"queued": 3}})
        if request.url.path == "/openviking/api/v1/system/wait":
            return httpx.Response(200, json={"result": {"status": "ok"}})
        return httpx.Response(404)

    client = OpenVikingHTTPClient(
        OpenVikingSettings(base_url="http://ov.local/openviking", api_key="secret"),
        transport=httpx.MockTransport(handler),
    )

    assert client.reindex("viking://resources/laws", mode="all", wait=False) == {"queued": 3}
    assert client.wait_processed(timeout=5) == {"status": "ok"}
    assert requests == [
        (
            "/openviking/api/v1/content/reindex",
            '{"uri":"viking://resources/laws","mode":"all","wait":false}',
        ),
        ("/openviking/api/v1/system/wait", '{"timeout":5}'),
    ]
