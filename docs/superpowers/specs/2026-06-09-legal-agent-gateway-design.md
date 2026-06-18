# LegalAgent Gateway Design

## Goal

Upgrade the existing legal assistant into a traceable legal agent platform with an internal LLM gateway, agent execution tracing, operational dashboard, evaluation history, and stronger legal-domain control.

## Scope

This design adds six capabilities to the current FastAPI + LangGraph + MCP + RAG system:

- LLM gateway for provider metadata, retry/fallback, latency logging, and token/cost fields.
- Agent trace persistence for chat runs, graph node events, tool events, retrieved laws, and final answers.
- Admin APIs and a lightweight dashboard for operational visibility.
- Evaluation result history stored in SQLite and exposed through admin APIs.
- Legal-domain analysis helpers for intent, fact completeness, citation validation, risk level, and evidence checklist.
- Documentation updates for project report and interview positioning.

The implementation stays inside the current monolith. It does not introduce a separate gateway service, user billing, or public API resale behavior.

## Architecture

The existing `data/docs.sqlite` metadata database becomes the operational store for observability tables. New service modules own the new boundaries:

- `services/gateway.py` wraps LangChain model invocation and records call attempts.
- `services/observability.py` owns SQLite tables and helpers for traces, events, LLM calls, and eval runs.
- `services/legal_analysis.py` owns deterministic legal-domain checks that are testable without LLM calls.
- `api/admin.py` exposes dashboard summary, recent traces, trace detail, LLM calls, and eval history.

`api/chat.py` creates one `trace_id` per chat request and streams the existing SSE output while recording observable events. `agent/nodes.py` passes the trace context into `get_llm()` and records citation guard results when the final answer is produced.

## Data Model

New SQLite tables:

- `llm_call_logs`: one row per LLM attempt with provider, model, latency, status, error, fallback source, and optional token fields.
- `agent_traces`: one row per chat request with trace id, thread id, user message, final answer, status, timings, and domain-analysis summary.
- `agent_events`: ordered trace events such as graph node output, tool start/end, retrieval, citation guard, fallback, and errors.
- `eval_runs`: one row per eval execution with mode, top_k, aggregate metrics, result path, and raw details JSON.

All JSON fields are stored as text using `json.dumps(..., ensure_ascii=False)`.

## Request Flow

1. `/api/chat` validates the request, creates a trace, and inserts a start event.
2. The request state includes `trace_id`.
3. `agent_node` calls the gateway-backed LLM factory.
4. `services/gateway.py` attempts the primary provider/model, logs the result, then tries configured fallback providers when configured.
5. SSE streaming continues as before.
6. Tool and final-answer events are persisted during streaming.
7. The trace is completed with final answer, status, elapsed time, and legal analysis.

## Legal-Domain Controls

`services/legal_analysis.py` provides deterministic helpers:

- `classify_legal_intent(text)` returns legal/non-legal intent and rough category.
- `check_fact_completeness(text)` returns missing fact dimensions for common legal scenarios.
- `validate_citations(answer, retrieved_laws)` returns verified and unsupported citations.
- `assess_risk_level(text)` returns low/medium/high based on scenario keywords.
- `build_evidence_checklist(text)` returns practical evidence items.

These helpers supplement the LLM prompt rather than replacing it.

## Dashboard

The dashboard is a static page at `/admin` backed by `/api/admin/*` endpoints. It shows:

- Total traces, success rate, average latency, fallback count.
- Recent traces and their node/tool events.
- Recent LLM calls.
- Evaluation history and aggregate metrics.

The page intentionally uses vanilla HTML/CSS/JS to match the current frontend stack.

## Testing

Tests cover:

- Database initialization and persistence helpers.
- Legal analysis helper behavior.
- Gateway fallback logging with fake model clients.
- Admin API summary shape.

Manual verification:

- Start the app.
- Send a legal chat message.
- Open `/admin`.
- Confirm the trace, LLM call, and events appear.
- Run retrieval eval and confirm eval history appears.

## Non-Goals

- No public key resale or payment system.
- No multi-tenant billing in this version.
- No full React/Vue dashboard rewrite.
- No replacement of the existing MCP tools or RAG retriever.
