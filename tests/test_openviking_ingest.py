from __future__ import annotations

import httpx

from services.openviking_ingest import (
    build_law_article_resource_specs,
    build_law_resource_specs,
    build_legal_skill_specs,
    import_law_article_resources,
    import_law_resources,
    import_legal_skills,
)


class RecordingOpenVikingClient:
    def __init__(self):
        self.resources = []
        self.skills = []
        self.writes = []
        self.reindexes = []

    def add_resource(self, path, *, to, reason="", wait=False, build_index=True):
        self.resources.append({
            "path": str(path),
            "to": to,
            "reason": reason,
            "wait": wait,
            "build_index": build_index,
        })
        return {"root_uri": to}

    def add_skill(self, data, *, wait=False):
        self.skills.append({"data": data, "wait": wait})
        return {"root_uri": f"viking://user/default/skills/{data['name']}"}

    def write(self, uri, content, *, mode="replace", wait=False, timeout=None):
        self.writes.append({
            "uri": uri,
            "content": content,
            "mode": mode,
            "wait": wait,
            "timeout": timeout,
        })
        return {"uri": uri}

    def reindex(self, uri, *, mode="vectors_only", wait=True):
        self.reindexes.append({
            "uri": uri,
            "mode": mode,
            "wait": wait,
        })
        return {"uri": uri, "mode": mode}


class UpsertOpenVikingClient(RecordingOpenVikingClient):
    def write(self, uri, content, *, mode="replace", wait=False, timeout=None):
        self.writes.append({
            "uri": uri,
            "content": content,
            "mode": mode,
            "wait": wait,
            "timeout": timeout,
        })
        if mode == "replace":
            request = httpx.Request("POST", "http://ov.local/api/v1/content/write")
            response = httpx.Response(404, request=request, json={"error": {"code": "NOT_FOUND"}})
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return {"uri": uri, "mode": mode}


def test_build_law_resource_specs_maps_law_files_to_stable_viking_uris(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text("第一条 劳动合同", encoding="utf-8")
    (tmp_path / "19_消费者权益保护法.txt").write_text("第一条 消费者", encoding="utf-8")
    (tmp_path / "02_民法典.txt").write_text("第一条 民事", encoding="utf-8")

    specs = build_law_resource_specs(tmp_path)

    by_name = {spec.law_name: spec for spec in specs}
    assert by_name["劳动合同法"].to_uri == "viking://resources/laws/labor/劳动合同法.txt"
    assert by_name["消费者权益保护法"].to_uri == "viking://resources/laws/consumer_protection/消费者权益保护法.txt"
    assert by_name["民法典"].to_uri == "viking://resources/laws/civil_code/民法典.txt"


def test_import_law_resources_calls_real_add_resource_contract(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text("第一条 劳动合同", encoding="utf-8")
    client = RecordingOpenVikingClient()

    result = import_law_resources(client, tmp_path, wait=True, build_index=True)

    assert result["imported"] == 1
    assert client.resources == [
        {
            "path": str(tmp_path / "08_劳动合同法.txt"),
            "to": "viking://resources/laws/labor/劳动合同法.txt",
            "reason": "中国法律语料导入：劳动合同法",
            "wait": True,
            "build_index": True,
        }
    ]


def test_build_law_article_resource_specs_creates_article_level_viking_uris(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text(
        "# 劳动合同法\n\n"
        "第一章 总则\n"
        "第十九条 劳动合同期限三个月以上不满一年的，试用期不得超过一个月。\n"
        "第二十条 劳动者在试用期的工资不得低于约定工资的百分之八十。\n",
        encoding="utf-8",
    )

    specs = build_law_article_resource_specs(tmp_path)

    assert [spec.chunk_id for spec in specs] == [
        "劳动合同法_第十九条",
        "劳动合同法_第二十条",
    ]
    assert specs[1].to_uri == "viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md"
    assert "chunk_id: 劳动合同法_第二十条" in specs[1].content
    assert "第二十条 劳动者在试用期的工资" in specs[1].content


def test_import_law_article_resources_writes_article_cards(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text(
        "第十九条 试用期约定规则。\n"
        "第二十条 试用期工资不得低于百分之八十。\n",
        encoding="utf-8",
    )
    client = RecordingOpenVikingClient()

    result = import_law_article_resources(client, tmp_path, wait=True)

    assert result["imported"] == 2
    assert client.writes[0]["uri"] == (
        "viking://resources/laws/labor/劳动合同法/劳动合同法_第十九条.md"
    )
    assert client.writes[0]["mode"] == "create"
    assert client.writes[0]["wait"] is True
    assert "law_name: 劳动合同法" in client.writes[0]["content"]
    assert client.reindexes == [
        {
            "uri": "viking://resources/laws",
            "mode": "vectors_only",
            "wait": True,
        }
    ]


def test_import_law_article_resources_can_skip_reindex(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text(
        "第二十条 试用期工资不得低于百分之八十。\n",
        encoding="utf-8",
    )
    client = RecordingOpenVikingClient()

    result = import_law_article_resources(client, tmp_path, build_index=False)

    assert result["imported"] == 1
    assert client.writes
    assert client.reindexes == []
    assert result["reindex_result"] is None


def test_import_law_article_resources_can_trigger_full_semantic_reindex(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text(
        "第二十条 试用期工资不得低于百分之八十。\n",
        encoding="utf-8",
    )
    client = RecordingOpenVikingClient()

    result = import_law_article_resources(client, tmp_path, reindex_mode="semantic_and_vectors", wait=True)

    assert result["imported"] == 1
    assert client.reindexes == [
        {
            "uri": "viking://resources/laws",
            "mode": "semantic_and_vectors",
            "wait": True,
        }
    ]


def test_import_law_article_resources_can_async_write_then_wait_reindex(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text(
        "第二十条 试用期工资不得低于百分之八十。\n",
        encoding="utf-8",
    )
    client = RecordingOpenVikingClient()

    import_law_article_resources(
        client,
        tmp_path,
        wait=True,
        write_wait=False,
        reindex_mode="all",
    )

    assert client.writes[0]["wait"] is False
    assert client.reindexes == [
        {
            "uri": "viking://resources/laws",
            "mode": "semantic_and_vectors",
            "wait": True,
        }
    ]


def test_import_law_article_resources_upsert_falls_back_to_create(tmp_path):
    (tmp_path / "08_劳动合同法.txt").write_text(
        "第二十条 试用期工资不得低于百分之八十。\n",
        encoding="utf-8",
    )
    client = UpsertOpenVikingClient()

    import_law_article_resources(client, tmp_path, mode="upsert", build_index=False)

    assert [item["mode"] for item in client.writes] == ["replace", "create"]


def test_build_legal_skill_specs_returns_openviking_skill_dicts():
    skills = build_legal_skill_specs()

    labor = next(skill for skill in skills if skill["name"] == "labor-arbitration-workflow")
    assert "description" in labor
    assert "content" in labor
    assert "劳动关系" in labor["content"]
    assert "legal" in labor["tags"]


def test_import_legal_skills_calls_add_skill():
    client = RecordingOpenVikingClient()

    result = import_legal_skills(client, wait=True)

    assert result["imported"] >= 5
    assert client.skills[0]["wait"] is True
    assert {"name", "description", "content", "tags"} <= set(client.skills[0]["data"])
