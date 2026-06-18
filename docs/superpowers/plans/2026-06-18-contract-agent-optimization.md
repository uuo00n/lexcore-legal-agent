# Contract Agent Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the phase-1 structured contract agent core and connect it to the existing report and `contract_agent_node` flow.

**Architecture:** Add a deterministic `services.contract_agent` package for schemas, clause segmentation, classification, checklists, scoring, grounding, revisions, formatting, and workflow orchestration. Keep the current chat/report APIs stable by adapting `services.contract_report` and `agent.nodes.contract_agent_node` to consume `ContractReviewResult`.

**Tech Stack:** Python 3, Pydantic v2, FastAPI service modules, LangGraph node integration, pytest.

---

### Task 1: Contract Agent Core Tests

**Files:**
- Create: `/Users/didi/Desktop/Legal/tests/test_contract_agent_core.py`
- Create: `/Users/didi/Desktop/Legal/tests/test_contract_agent_workflow.py`
- Modify: `/Users/didi/Desktop/Legal/tests/test_contract_report.py`
- Modify: `/Users/didi/Desktop/Legal/tests/test_supervisor_nodes.py`

- [ ] Write failing tests for clause segmentation, contract classification, scoring, grounding, prompt-injection handling, NDA/lease/service risk detection, missing clauses, report rendering, and `contract_agent_node` structured report payload.
- [ ] Run focused tests and confirm they fail because `services.contract_agent` does not exist or the old report service lacks the new structured fields.

### Task 2: Schema, Segmentation, Classification, And Checklists

**Files:**
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/__init__.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/schema.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/clause_segmenter.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/classifier.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/checklists/__init__.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/checklists/generic.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/checklists/nda.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/checklists/lease.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/checklists/service.py`

- [ ] Implement Pydantic schemas matching the design doc.
- [ ] Implement deterministic clause segmentation with source offsets and quote-preserving text.
- [ ] Implement keyword-based contract classification for NDA, lease, service, SaaS, loan, data processing, and unknown.
- [ ] Implement generic, NDA, lease, and service checklist items with risk patterns, missing-clause markers, suggested fixes, and proposed text templates.
- [ ] Run focused core tests and confirm schema/segmentation/classification/checklist tests pass.

### Task 3: Scoring, Grounding, Revisions, And Workflow

**Files:**
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/scoring.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/grounding.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/revisions.py`
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/workflow.py`

- [ ] Implement impact/likelihood/detectability scoring with the design-doc severity thresholds.
- [ ] Implement grounding verification for quote existence, missing-clause quote prevention, proposed-text separation, and prompt-injection-as-content handling.
- [ ] Implement revision generation for high and important medium risks.
- [ ] Implement `run_contract_agent(input: ContractAgentInput) -> ContractReviewResult`.
- [ ] Run workflow tests and confirm contract review, risk scan, contract QA, contract summary, missing-clause check, and unsupported version/draft task behavior pass.

### Task 4: Report Rendering And Node Integration

**Files:**
- Create: `/Users/didi/Desktop/Legal/services/contract_agent/formatter.py`
- Modify: `/Users/didi/Desktop/Legal/services/contract_report.py`
- Modify: `/Users/didi/Desktop/Legal/agent/nodes.py`

- [ ] Render Markdown reports from `ContractReviewResult` while preserving `render_contract_report()` and `save_contract_report()` public API compatibility.
- [ ] Keep `analyze_contract_text()` compatibility for existing callers by returning dicts derived from the structured result.
- [ ] Update `contract_agent_node` to include `contract_result`, `contract_meta`, top issues, overall risk, report ID, and download URL in its `agent_reports` payload.
- [ ] Keep missing-document behavior compatible with existing tests.
- [ ] Run report and supervisor-node focused tests.

### Task 5: Verification

**Files:**
- Test: `/Users/didi/Desktop/Legal/tests/test_contract_agent_core.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_contract_agent_workflow.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_contract_report.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_reports_api.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_supervisor_nodes.py`

- [ ] Run the focused pytest set for contract agent/report/node/API behavior.
- [ ] Run `git diff --check`.
- [ ] Inspect git diff and confirm only contract-agent implementation, report integration, node integration, tests, and this plan were changed.
- [ ] Report any pre-existing dirty worktree constraints clearly.
