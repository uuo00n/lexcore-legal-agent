"""管辖与办理路径 Service Layer。"""
from __future__ import annotations

import json
import re
from typing import Any


_CATEGORY_KEYWORDS = {
    "labor": ["劳动", "工资", "社保", "工伤", "辞退", "不续签", "劳动仲裁", "加班"],
    "contract": ["合同", "买卖", "借款", "货款", "服务费", "违约", "定金", "协议"],
    "lease": ["租房", "租赁", "房租", "押金", "退租", "房东"],
    "tort": ["侵权", "受伤", "交通事故", "打人", "名誉权", "损害赔偿"],
    "consumer": ["消费者", "退款", "商家", "平台", "网购", "质量问题", "虚假宣传"],
    "criminal": ["报警", "诈骗", "盗窃", "故意伤害", "刑事", "拘留", "犯罪"],
    "administrative": ["行政处罚", "罚款", "行政复议", "行政诉讼", "处罚决定"],
    "family": ["离婚", "抚养", "彩礼", "夫妻", "婚姻", "继承"],
}


def _classify_case(case_type: str, parties: str, contract_clause: str) -> str:
    source = f"{case_type} {parties} {contract_clause}"
    scores = {
        category: sum(1 for keyword in keywords if keyword in source)
        for category, keywords in _CATEGORY_KEYWORDS.items()
    }
    category, score = max(scores.items(), key=lambda item: item[1])
    return category if score > 0 else "general"


def _extract_agreed_court(contract_clause: str) -> str | None:
    if not contract_clause:
        return None
    patterns = [
        r"由([^，。,；;\n]{2,40}?人民法院)管辖",
        r"提交([^，。,；;\n]{2,40}?人民法院)",
        r"向([^，。,；;\n]{2,40}?人民法院)起诉",
    ]
    for pattern in patterns:
        match = re.search(pattern, contract_clause)
        if match:
            return match.group(1).strip()
    return None


def _route(
    *,
    route_type: str,
    priority: int,
    authority_type: str,
    suggested_place: str,
    jurisdiction_basis: list[str],
    action: str,
    materials: list[str] | None = None,
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "route_type": route_type,
        "priority": priority,
        "authority_type": authority_type,
        "suggested_place": suggested_place,
        "jurisdiction_basis": jurisdiction_basis,
        "action": action,
        "required_materials": materials or ["身份证明", "事实经过说明", "证据材料"],
        "caveats": caveats or [],
    }


def _common_missing(location: str, parties: str) -> list[str]:
    missing: list[str] = []
    if not location.strip():
        missing.append("地点信息，例如合同履行地、侵权行为地、工作地或被告住所地")
    if not parties.strip():
        missing.append("双方身份和所在地，例如自然人住所地、公司注册地或用人单位所在地")
    return missing


def _build_routes(
    *,
    category: str,
    case_type: str,
    location: str,
    parties: str,
    contract_clause: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    location_hint = location.strip() or "相关地点"
    agreed_court = _extract_agreed_court(contract_clause)
    routes: list[dict[str, Any]] = []
    warnings: list[str] = []
    missing = _common_missing(location, parties)

    if agreed_court and category in {"contract", "lease"}:
        routes.append(_route(
            route_type="agreed_jurisdiction",
            priority=1,
            authority_type="人民法院",
            suggested_place=agreed_court,
            jurisdiction_basis=["合同约定管辖法院"],
            action="先核对合同争议解决条款，再准备起诉材料向约定法院立案。",
            caveats=["需确认约定法院与争议有实际联系，且不违反级别管辖和专属管辖规则。"],
        ))
        warnings.append("合同约定管辖需确认是否与争议有实际联系，并且不得违反级别管辖、专属管辖。")

    if category == "labor":
        routes.extend([
            _route(
                route_type="labor_arbitration",
                priority=1,
                authority_type="劳动争议仲裁委员会",
                suggested_place=f"{location_hint}的劳动合同履行地或用人单位所在地劳动争议仲裁委员会",
                jurisdiction_basis=["劳动合同履行地", "用人单位所在地"],
                action="劳动争议通常先申请劳动仲裁；对仲裁结果不服，再按规则向法院起诉。",
                materials=["身份证明", "劳动合同或入职证明", "工资流水", "考勤记录", "解除/不续签通知或沟通记录"],
            ),
            _route(
                route_type="labor_administration_complaint",
                priority=2,
                authority_type="劳动监察部门",
                suggested_place=f"{location_hint}的人力资源和社会保障局劳动监察机构",
                jurisdiction_basis=["用工行为发生地", "用人单位所在地"],
                action="欠薪、违法用工、社保投诉等，可同步向劳动监察投诉。",
                materials=["身份证明", "工资流水", "欠薪记录", "用工证据", "投诉事项说明"],
            ),
        ])
    elif category in {"contract", "lease"}:
        if not agreed_court:
            routes.append(_route(
                route_type="civil_litigation",
                priority=1,
                authority_type="人民法院",
                suggested_place=f"{location_hint}的被告住所地或合同履行地人民法院",
                jurisdiction_basis=["被告住所地", "合同履行地"],
                action="先核对合同是否有有效管辖约定；没有约定时，通常按被告住所地或合同履行地起诉。",
                materials=["身份证明", "合同或订单", "履行凭证", "付款记录", "催告或沟通记录"],
            ))
        if category == "lease":
            routes.append(_route(
                route_type="lease_mediation_or_complaint",
                priority=2,
                authority_type="街道/住建/市场监管等调解或投诉渠道",
                suggested_place=f"{location_hint}的房屋所在地基层调解组织或相关主管部门",
                jurisdiction_basis=["房屋所在地", "租赁合同履行地"],
                action="押金、维修、退租交接等争议，可先尝试调解；调解不成再起诉。",
            ))
    elif category == "tort":
        routes.append(_route(
            route_type="tort_litigation",
            priority=1,
            authority_type="人民法院",
            suggested_place=f"{location_hint}的侵权行为地或被告住所地人民法院",
            jurisdiction_basis=["侵权行为地", "被告住所地"],
            action="人身或财产损害赔偿，一般向侵权行为地或被告住所地法院起诉。",
            materials=["身份证明", "损害后果证明", "医疗或维修票据", "报警记录", "照片视频或证人线索"],
        ))
    elif category == "consumer":
        routes.extend([
            _route(
                route_type="consumer_complaint",
                priority=1,
                authority_type="市场监督管理部门或消费者协会",
                suggested_place=f"{location_hint}的市场监督管理部门、12315 平台或消费者协会",
                jurisdiction_basis=["经营者所在地", "消费行为发生地", "平台经营者所在地"],
                action="先投诉并固定订单、聊天记录、付款和商品证据；协商不成再考虑诉讼。",
                materials=["订单信息", "付款凭证", "商品或服务问题证据", "沟通记录"],
            ),
            _route(
                route_type="consumer_litigation",
                priority=2,
                authority_type="人民法院",
                suggested_place=f"{location_hint}的被告住所地或合同履行地人民法院",
                jurisdiction_basis=["被告住所地", "合同履行地"],
                action="金额较大或投诉无果时，可准备材料起诉经营者或平台责任主体。",
            ),
        ])
    elif category == "criminal":
        routes.append(_route(
            route_type="public_security_report",
            priority=1,
            authority_type="公安机关",
            suggested_place=f"{location_hint}的案发地、违法犯罪行为发生地或嫌疑人所在地公安机关/派出所",
            jurisdiction_basis=["案发地", "违法犯罪行为发生地", "嫌疑人所在地"],
            action="涉及人身危险、诈骗、盗窃、故意伤害等刑事风险，优先报警并保全证据。",
            materials=["身份证明", "事实经过", "转账记录", "聊天记录", "伤情或损失证明"],
        ))
    elif category == "administrative":
        routes.extend([
            _route(
                route_type="administrative_reconsideration",
                priority=1,
                authority_type="行政复议机关",
                suggested_place=f"{location_hint}的作出处罚机关的本级人民政府或上一级主管部门",
                jurisdiction_basis=["作出行政行为的机关", "复议管辖规则"],
                action="收到行政处罚或具体行政行为后，先核对复议期限和复议机关。",
                materials=["行政处罚决定书", "身份证明", "事实和理由", "证据材料"],
            ),
            _route(
                route_type="administrative_litigation",
                priority=2,
                authority_type="人民法院",
                suggested_place=f"{location_hint}的作出行政行为机关所在地人民法院",
                jurisdiction_basis=["行政机关所在地", "行政诉讼管辖规则"],
                action="不复议或复议后仍不服的，按行政诉讼期限准备起诉。",
            ),
        ])
    elif category == "family":
        routes.append(_route(
            route_type="family_litigation",
            priority=1,
            authority_type="人民法院",
            suggested_place=f"{location_hint}的被告住所地或经常居住地人民法院",
            jurisdiction_basis=["被告住所地", "经常居住地"],
            action="离婚、抚养、继承等家事纠纷，通常向被告住所地或经常居住地法院起诉。",
            materials=["身份关系证明", "婚姻或亲属关系材料", "财产和子女情况证据", "沟通记录"],
        ))
    else:
        routes.append(_route(
            route_type="general_consultation",
            priority=1,
            authority_type="待确定",
            suggested_place=f"需结合{case_type or '案件类型'}、{location_hint}和相对方所在地进一步判断",
            jurisdiction_basis=["案件性质", "被告住所地", "行为发生地", "合同履行地"],
            action="请补充案件类型、地点、对方身份和是否有合同约定后，再判断具体办理机关。",
        ))

    return routes, missing, warnings


def jurisdiction_route_service(
    case_type: str,
    location: str = "",
    parties: str = "",
    contract_clause: str = "",
) -> str:
    """
    函数作用：
        根据案件类型、地点、双方身份和合同约定，判断常见办理机关和管辖连接点。
    输入参数：
        - case_type: str
        - location: str，默认值 ''
        - parties: str，默认值 ''
        - contract_clause: str，默认值 ''
    输出参数：
        - str
    """
    category = _classify_case(case_type, parties, contract_clause)
    routes, missing, warnings = _build_routes(
        category=category,
        case_type=case_type,
        location=location,
        parties=parties,
        contract_clause=contract_clause,
    )
    payload = {
        "status": "found" if routes else "needs_more_facts",
        "case_type": case_type,
        "case_category": category,
        "location": location,
        "parties": parties,
        "contract_clause": contract_clause,
        "routes": sorted(routes, key=lambda item: item["priority"]),
        "missing_facts": missing,
        "warnings": warnings,
        "disclaimer": "本工具给出常见办理路径和管辖连接点，具体立案口径以当地机关或法院要求为准。",
    }
    return json.dumps(payload, ensure_ascii=False)



__all__ = ["jurisdiction_route_service"]
