"""合同审查清单选择。"""
from __future__ import annotations

from services.contract_agent.checklists.generic import GENERIC_CHECKLIST
from services.contract_agent.checklists.lease import LEASE_CHECKLIST
from services.contract_agent.checklists.nda import NDA_CHECKLIST
from services.contract_agent.checklists.service import SERVICE_CHECKLIST
from services.contract_agent.schema import ChecklistItem, ContractTaskType, ContractType


_BY_TYPE = {
    "nda": NDA_CHECKLIST,
    "lease": LEASE_CHECKLIST,
    "service": SERVICE_CHECKLIST,
    "software_development": SERVICE_CHECKLIST,
    "saas": SERVICE_CHECKLIST,
}


def select_checklist(contract_type: ContractType, task_type: ContractTaskType) -> list[ChecklistItem]:
    """
    函数作用：
        根据合同类型和任务类型选择结构化审查清单。
    """
    items = list(GENERIC_CHECKLIST)
    items.extend(_BY_TYPE.get(contract_type, []))
    if task_type == "contract_qa":
        return [item for item in items if item.category in {"termination", "payment", "breach", "delivery_acceptance"}]
    if task_type == "missing_clause_check":
        return [item for item in items if item.missing_clause_risk]
    return items
