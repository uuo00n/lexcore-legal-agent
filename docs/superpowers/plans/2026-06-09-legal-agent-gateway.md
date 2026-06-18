# LegalAgent Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM gateway observability, agent traces, dashboard, eval history, and legal-domain controls to the existing legal assistant.

**Architecture:** Add focused service modules around the existing FastAPI/LangGraph app. Persist observability data in the existing metadata SQLite database and expose it through new admin APIs plus a static dashboard.

**Tech Stack:** FastAPI, SQLite, LangGraph, LangChain ChatOpenAI, vanilla HTML/CSS/JS, pytest.

---

### Task 1: Observability Storage

**Files:**
- Create: `/Users/didi/Desktop/Legal/services/observability.py`
- Modify: `/Users/didi/Desktop/Legal/main.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_observability.py`

- [ ] Create SQLite tables for `llm_call_logs`, `agent_traces`, `agent_events`, and `eval_runs`.
- [ ] Add helper functions to create/update traces, append events, record LLM calls, record eval runs, and query dashboard data.
- [ ] Initialize these tables in FastAPI lifespan after `init_meta_db()`.
- [ ] Add unit tests using a temporary `DOCS_DB`.

### Task 2: Legal Analysis Helpers

**Files:**
- Create: `/Users/didi/Desktop/Legal/services/legal_analysis.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_legal_analysis.py`

- [ ] Implement legal intent classification using scenario keywords.
- [ ] Implement fact completeness checks for labor, lease, debt, injury, contract, marriage, and general disputes.
- [ ] Implement citation validation against retrieved laws.
- [ ] Implement simple risk-level and evidence-checklist helpers.
- [ ] Add tests for representative legal and non-legal messages.

### Task 3: LLM Gateway Wrapper

**Files:**
- Modify: `/Users/didi/Desktop/Legal/services/llm.py`
- Create: `/Users/didi/Desktop/Legal/services/gateway.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_gateway.py`

- [ ] Add provider resolution helpers that can build primary and fallback model configs.
- [ ] Add a `GatewayChatModel` wrapper that records latency, status, error, and fallback source around `ainvoke()`.
- [ ] Keep `get_llm()` backward-compatible and add optional `trace_id` and `thread_id` parameters.
- [ ] Preserve `.bind_tools()` support by returning a wrapped bound model.
- [ ] Add tests with fake clients to verify fallback logging.

### Task 4: Agent Trace Integration

**Files:**
- Modify: `/Users/didi/Desktop/Legal/agent/state.py`
- Modify: `/Users/didi/Desktop/Legal/agent/nodes.py`
- Modify: `/Users/didi/Desktop/Legal/api/chat.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_agent_trace.py`

- [ ] Add `trace_id` to `AgentState`.
- [ ] Create traces at chat start and complete them at stream end.
- [ ] Record SSE-visible tool start/end events.
- [ ] Record node-level final answer and citation guard events.
- [ ] Store legal-domain analysis on trace completion.

### Task 5: Admin API and Dashboard

**Files:**
- Create: `/Users/didi/Desktop/Legal/api/admin.py`
- Modify: `/Users/didi/Desktop/Legal/main.py`
- Create: `/Users/didi/Desktop/Legal/static/admin.html`
- Create: `/Users/didi/Desktop/Legal/static/admin.js`
- Create: `/Users/didi/Desktop/Legal/static/admin.css`
- Test: `/Users/didi/Desktop/Legal/tests/test_admin_api.py`

- [ ] Add `/api/admin/summary`, `/api/admin/traces`, `/api/admin/traces/{trace_id}`, `/api/admin/llm-calls`, and `/api/admin/eval-runs`.
- [ ] Add `/admin` route returning the dashboard page.
- [ ] Build a compact dashboard showing metrics, recent traces, trace details, LLM calls, and eval history.
- [ ] Add API tests for response shape.

### Task 6: Eval History

**Files:**
- Modify: `/Users/didi/Desktop/Legal/eval/run_eval.py`
- Test: `/Users/didi/Desktop/Legal/tests/test_eval_history.py`

- [ ] Initialize metadata DB and observability tables in eval script.
- [ ] Record saved eval results into `eval_runs`.
- [ ] Preserve the existing JSON result output.
- [ ] Add a test around `save_results()` using a temporary result path when practical.

### Task 7: Documentation

**Files:**
- Modify: `/Users/didi/Desktop/Legal/docs/report/project-report.md`

- [ ] Add a section for LLM Gateway.
- [ ] Add a section for Agent Trace and dashboard.
- [ ] Add a section for eval history and legal-domain controls.
- [ ] Add interview-facing project summary.

### Task 8: Verification

**Commands:**
- `pytest -q`
- `python eval/run_eval.py --mode retrieval --top-k 3`
- `python -m uvicorn main:app --reload`

- [ ] Run tests.
- [ ] Run retrieval eval.
- [ ] Start local server and open `/admin`.
- [ ] Send one legal chat request and confirm trace/log/dashboard data.
