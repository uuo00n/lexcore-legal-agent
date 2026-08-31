"""Contract review agent node."""
from __future__ import annotations

import os
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.node_utils import (
    compatibility_dependency,
    latest_human_message,
    record_trace_event,
)
from agent.state import AgentState
from services.contract_agent.formatter import render_chat_summary
from services.contract_agent.schema import ContractReviewResult
from services.contract_report import save_contract_report
from services.llm import get_llm


async def _llm_contract_summary(state: AgentState, markdown: str) -> str:
    """Generate a short summary of the deterministic contract report."""
    try:
        llm_factory = compatibility_dependency("get_llm", get_llm)
        llm = llm_factory(
            provider=os.getenv("CONTRACT_AGENT_PROVIDER", "deepseek"),
            model=os.getenv("CONTRACT_AGENT_MODEL", "deepseek-v4-pro"),
            model_route="contract_agent",
            trace_id=state.get("trace_id"),
            thread_id=state.get("thread_id"),
            temperature=0.2,
            streaming=False,
        )
        response = await llm.ainvoke([
            SystemMessage(content="你是合同审查智能体。请根据报告生成 3 条以内的中文摘要，不要编造报告外内容。"),
            HumanMessage(content=markdown[:4000]),
        ])
        return (response.content or "").strip()
    except Exception as exc:
        record_trace_event(
            state.get("trace_id"),
            "agent_fallback",
            name="contract_agent",
            payload={"error": str(exc)},
        )
        return ""


async def contract_agent_node(state: AgentState) -> dict[str, Any]:
    """Create a structured contract review report from an uploaded document."""
    doc_text = state.get("uploaded_doc_text")
    doc_name = state.get("uploaded_doc_name") or "合同文档"
    latest_query = latest_human_message(state)
    if not doc_text:
        try:
            llm_factory = compatibility_dependency("get_llm", get_llm)
            llm = llm_factory(
                provider=os.getenv("CONTRACT_AGENT_PROVIDER", "zhipu"),
                model=os.getenv("CONTRACT_AGENT_MODEL", "glm-4.7"),
                model_route="contract_agent",
                trace_id=state.get("trace_id"),
                thread_id=state.get("thread_id"),
                temperature=0.2,
                streaming=False,
            )
            response = await llm.ainvoke([
                SystemMessage(content="你是合同审查智能体。用户想审查合同但没有上传文档，请提示上传合同并说明你会输出什么。"),
                HumanMessage(content=latest_query),
            ])
            content = (response.content or "").strip()
        except Exception:
            content = (
                "可以，我会按合同审查流程处理。请先上传合同文件，"
                "我会生成风险条款、修改建议、补充材料清单和 Markdown 审查报告。"
            )
        record_trace_event(
            state.get("trace_id"),
            "contract_agent",
            name="missing_document",
            payload={"message": content},
        )
        return {
            "agent_reports": [{
                "agent": "contract_agent",
                "status": "missing_document",
                "summary": "用户需要合同审查但尚未上传合同文档",
                "draft_response": content,
                "next_steps": ["上传合同文件"],
                "confidence": "high",
            }],
        }

    report = save_contract_report(doc_name, doc_text, latest_query)
    contract_result_data = report.get("contract_result") or {}
    contract_result = ContractReviewResult.model_validate(contract_result_data)
    summary = await _llm_contract_summary(state, report["markdown"])
    download_url = f"/api/reports/{report['report_id']}"
    content = render_chat_summary(
        contract_result,
        report_id=report["report_id"],
        download_url=download_url,
    )
    if summary:
        content += f"\n\n【合同审查摘要】\n{summary}"
    record_trace_event(
        state.get("trace_id"),
        "contract_agent",
        name="contract_review_report",
        payload={
            "report_id": report["report_id"],
            "download_url": download_url,
            "contract_type": contract_result.contract_meta.contract_type,
            "overall_risk_level": contract_result.executive_summary.overall_risk_level,
            "preview": report["markdown"][:800],
        },
    )
    return {
        "agent_reports": [{
            "agent": "contract_agent",
            "status": "report_ready",
            "summary": summary or "合同审查报告已生成",
            "draft_response": content,
            "report_id": report["report_id"],
            "download_url": download_url,
            "contract_meta": contract_result.contract_meta.model_dump(mode="json"),
            "overall_risk_level": contract_result.executive_summary.overall_risk_level,
            "top_issues": [
                issue.model_dump(mode="json") for issue in contract_result.issues[:3]
            ],
            "contract_result": contract_result.model_dump(mode="json"),
            "preview": report["markdown"][:1200],
            "confidence": "high",
        }],
    }
