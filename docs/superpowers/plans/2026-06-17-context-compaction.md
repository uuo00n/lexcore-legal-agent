# Context Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add runtime context compaction, context usage visibility, and a manual compact action to the Legal agent.

**Architecture:** Implement a focused `services.context_compaction` module, expose a thin LangGraph node in `agent.nodes`, add thread API endpoints, and update the static frontend meter. Keep existing memory extraction as the post-response long-term memory path.

**Tech Stack:** FastAPI, LangGraph, LangChain messages, SQLite memory tables, vanilla JS frontend, pytest.

---

### Task 1: Backend Compaction Service

**Files:**
- Create: `services/context_compaction.py`
- Test: `tests/test_context_compaction.py`

- [ ] Write tests for usage estimation, threshold decisions, entity merge, and message removal.
- [ ] Implement dataclasses and helpers in `services/context_compaction.py`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_context_compaction.py -q`.

### Task 2: LangGraph Node

**Files:**
- Modify: `agent/state.py`
- Modify: `agent/nodes.py`
- Modify: `agent/graph.py`
- Test: `tests/test_context_compaction.py`

- [ ] Add context status fields to `AgentState`.
- [ ] Add `context_compaction_node`.
- [ ] Insert `context_compaction` before `memory` in `build_graph`.
- [ ] Run focused graph tests.

### Task 3: API Endpoints and SSE Status

**Files:**
- Modify: `api/threads.py`
- Modify: `api/chat.py`
- Test: `tests/test_context_compaction.py`
- Test: `tests/test_chat_process_events.py`

- [ ] Add `GET /threads/{thread_id}/context`.
- [ ] Add `POST /threads/{thread_id}/compact`.
- [ ] Emit `context_status` SSE events when the graph node reports usage.
- [ ] Run focused API/SSE tests.

### Task 4: Frontend Meter

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`

- [ ] Add a compact context meter in the sidebar.
- [ ] Add a manual compact button.
- [ ] Refresh status on startup, thread switch, after chat, and after manual compact.

### Task 5: Verification

**Files:**
- Existing test suite.

- [ ] Run `./.venv/bin/python -m pytest tests/test_context_compaction.py tests/test_chat_process_events.py tests/test_supervisor_nodes.py tests/test_memory_extractor.py tests/test_threads_history.py -q`.
- [ ] Inspect `git diff -- services/context_compaction.py agent/nodes.py agent/graph.py agent/state.py api/chat.py api/threads.py static/app.js static/index.html static/style.css tests/test_context_compaction.py docs/superpowers/specs/2026-06-17-context-compaction-design.md docs/superpowers/plans/2026-06-17-context-compaction.md`.
