# Supervisor Multi-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the current legal assistant graph into a Supervisor multi-agent architecture with `fact_agent`, `contract_agent`, and `legal_consult_agent`.

**Architecture:** Keep deterministic services for retrieval, citation guards, cache, quota, metrics, and reports. Add a supervisor routing service and LangGraph nodes that route requests to the correct business agent.

**Tech Stack:** FastAPI, LangGraph, SQLite observability, existing RAG/MCP tools, pytest.

---

### Task 1: Supervisor Routing Service

**Files:**
- Create: `/Users/didi/Desktop/Legal/services/supervisor.py`
- Modify: `/Users/didi/Desktop/Legal/agent/state.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_supervisor.py`

- [ ] Add route labels: `fact_agent`, `contract_agent`, `legal_consult_agent`.
- [ ] Route contract/document requests to `contract_agent`.
- [ ] Route sparse legal questions to `fact_agent`.
- [ ] Route normal legal questions and simple legal questions to `legal_consult_agent`.

### Task 2: LangGraph Multi-Agent Nodes

**Files:**
- Modify: `/Users/didi/Desktop/Legal/agent/nodes.py`
- Modify: `/Users/didi/Desktop/Legal/agent/graph.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_supervisor_nodes.py`

- [ ] Add `supervisor_agent_node`.
- [ ] Rename/use existing fact check behavior as `fact_agent_node`.
- [ ] Add `contract_agent_node` that creates a concise contract review answer using uploaded docs or asks for a document.
- [ ] Keep existing ReAct node as `legal_consult_agent_node`.
- [ ] Add routing condition from supervisor to the three business agents.

### Task 3: API/Trace Integration

**Files:**
- Modify: `/Users/didi/Desktop/Legal/api/chat.py`
- Modify: `/Users/didi/Desktop/Legal/services/observability.py`

- [ ] Treat `fact_agent`, `contract_agent`, and `legal_consult_agent` outputs as answer-producing nodes.
- [ ] Add readable trace labels for supervisor and the three agents.

### Task 4: Docs and Verification

**Files:**
- Modify: `/Users/didi/Desktop/Legal/docs/report/project-report.md`

- [ ] Document the Supervisor multi-agent architecture.
- [ ] Run focused tests.
- [ ] Run full test suite.
- [ ] Restart local service and smoke test `/api/health`.
