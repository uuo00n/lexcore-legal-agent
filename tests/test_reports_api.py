from __future__ import annotations

import pytest

from services.checkpoint import init_meta_db, reset_for_tests, save_doc


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


@pytest.mark.asyncio
async def test_create_contract_report_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DB", str(tmp_path / "meta.sqlite"))
    monkeypatch.chdir(tmp_path)
    init_meta_db()
    save_doc("doc-1", "合同.txt", "甲方可以随时解除合同，无需通知乙方。", False)

    from api.reports import ContractReportRequest, create_contract_report

    result = await create_contract_report(ContractReportRequest(doc_id="doc-1"))

    assert result["report_id"].startswith("contract-")
    assert "合同审查报告" in result["markdown"]
