from __future__ import annotations

import json


def test_jurisdiction_route_for_labor_dispute():
    from mcp_server.tools.jurisdiction import jurisdiction_route

    payload = json.loads(
        jurisdiction_route(
            case_type="劳动合同到期不续签补偿",
            location="北京市海淀区",
            parties="劳动者在北京市海淀区工作，用人单位注册在北京市朝阳区",
        )
    )

    assert payload["case_category"] == "labor"
    assert payload["routes"][0]["authority_type"] == "劳动争议仲裁委员会"
    assert "劳动合同履行地" in payload["routes"][0]["jurisdiction_basis"]
    assert "用人单位所在地" in payload["routes"][0]["jurisdiction_basis"]
    assert "劳动监察" in payload["routes"][1]["authority_type"]
    assert payload["missing_facts"] == []


def test_jurisdiction_route_respects_contract_jurisdiction_clause():
    from mcp_server.tools.jurisdiction import jurisdiction_route

    payload = json.loads(
        jurisdiction_route(
            case_type="买卖合同货款纠纷",
            location="上海市浦东新区",
            parties="原告在上海，被告在苏州",
            contract_clause="双方约定由上海市浦东新区人民法院管辖",
        )
    )

    assert payload["case_category"] == "contract"
    assert payload["routes"][0]["route_type"] == "agreed_jurisdiction"
    assert payload["routes"][0]["authority_type"] == "人民法院"
    assert "上海市浦东新区人民法院" in payload["routes"][0]["suggested_place"]
    assert "实际联系" in "".join(payload["warnings"])
