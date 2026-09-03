from __future__ import annotations

import pytest

from infrastructure.operational_store import InMemoryOperationalStore, init_operational_store
from services.checkpoint import reset_for_tests, save_doc


def setup_function():
    reset_for_tests()
    init_operational_store(InMemoryOperationalStore())


def teardown_function():
    reset_for_tests()


@pytest.mark.asyncio
async def test_create_contract_report_api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_doc("doc-1", "合同.txt", "甲方可以随时解除合同，无需通知乙方。", False)

    from api.reports import ContractReportRequest, create_contract_report

    result = await create_contract_report(ContractReportRequest(doc_id="doc-1"))

    assert result["report_id"].startswith("contract-")
    assert "合同审查报告" in result["markdown"]
