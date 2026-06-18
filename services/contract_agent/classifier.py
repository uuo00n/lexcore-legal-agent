"""合同类型识别。"""
from __future__ import annotations

from services.contract_agent.schema import ContractClassification, ContractType


_TYPE_SIGNALS: dict[ContractType, list[str]] = {
    "nda": ["保密协议", "保密信息", "披露方", "接收方", "保密期限", "保密义务"],
    "lease": ["租赁合同", "出租方", "承租方", "租金", "押金", "房屋", "退租"],
    "service": ["服务合同", "技术服务", "服务内容", "服务费", "交付成果", "验收"],
    "saas": ["SaaS", "订阅", "账号", "服务可用性", "SLA", "数据删除"],
    "software_development": ["软件开发", "源代码", "开发成果", "需求文档"],
    "loan": ["借款", "贷款", "本金", "利息", "还款", "担保"],
    "data_processing": ["个人信息", "数据处理", "处理目的", "处理范围", "泄露通知"],
    "labor": ["劳动合同", "试用期", "工资", "社保", "用人单位"],
    "sales": ["买卖合同", "货物", "交货", "价款", "卖方", "买方"],
    "ip_license": ["知识产权许可", "许可使用", "授权范围", "著作权", "商标"],
    "employment": ["聘用", "雇佣", "岗位职责", "薪酬"],
    "construction": ["施工", "工程", "竣工", "建设"],
    "settlement": ["和解", "调解", "一次性支付", "互不追究"],
    "partnership": ["合伙", "利润分配", "出资"],
    "agency": ["代理", "委托权限", "代理费"],
    "distribution": ["经销", "分销", "渠道", "区域"],
}


def classify_contract(text: str) -> ContractClassification:
    """
    函数作用：
        基于关键词信号识别合同类型。
    """
    source = text or ""
    scored: list[tuple[ContractType, list[str]]] = []
    for contract_type, signals in _TYPE_SIGNALS.items():
        hits = [signal for signal in signals if signal.lower() in source.lower()]
        if hits:
            scored.append((contract_type, hits))
    if not scored:
        return ContractClassification(contract_type="unknown", confidence=0.2, matched_signals=[])

    scored.sort(key=lambda item: (len(item[1]), len("".join(item[1]))), reverse=True)
    contract_type, hits = scored[0]
    confidence = min(0.95, 0.35 + len(hits) * 0.18)
    return ContractClassification(contract_type=contract_type, confidence=confidence, matched_signals=hits)
