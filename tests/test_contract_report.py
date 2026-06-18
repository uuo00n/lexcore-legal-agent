from __future__ import annotations

from services.contract_report import analyze_contract_text, render_contract_report, save_contract_report


def test_analyze_contract_text_detects_common_risk():
    result = analyze_contract_text("甲方可以随时解除合同，无需通知乙方。乙方逾期付款需承担每日百分之一违约金。")

    risks = " ".join(item["risk"] for item in result["findings"])
    assert "单方解除" in risks
    assert "违约金过高" in risks


def test_render_and_save_contract_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    markdown = render_contract_report("合同.txt", "甲方可以随时解除合同，无需通知乙方。")
    saved = save_contract_report("合同.txt", "甲方可以随时解除合同，无需通知乙方。")

    assert "# 合同审查报告" in markdown
    assert "合同类型" in markdown
    assert "风险评分" in markdown
    assert saved["report_id"].startswith("contract-")
    assert "单方解除" in saved["markdown"]


def test_analyze_contract_text_exposes_structured_contract_result():
    result = analyze_contract_text("技术服务合同。甲方可以随时解除合同，无需通知乙方。")

    assert result["contract_result"]["contract_meta"]["contract_type"] == "service"
    assert result["findings"][0]["severity"] in {"medium", "high", "critical"}
    assert "risk_score" in result["findings"][0]
