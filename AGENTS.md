# Repository Guidelines

## Project Structure & Module Organization

`main.py` creates the FastAPI application and LangGraph workflow. Graph state, topology, prompts, nodes, and tool wrappers live in `agent/`. HTTP routes are in `api/`; reusable logic belongs in `services/`. Retrieval is split by stage: `services/indexer/` chunks and builds the index, `services/rag/` holds the hybrid pipeline (Qdrant store, BM25, RRF fusion, reranker), and `services/retriever/hyde.py` handles query enhancement. Storage adapters and ORM models live in `infrastructure/`. FastMCP implementations are under `mcp_server/`; `run_mcp.py` is their entry point and runs as an independent process, not a child of the API. Put tests in `tests/test_*.py`, documentation in `docs/`, browser assets in `static/`, and legal corpus files in `data/laws/`.

## Build, Test, and Development Commands

- `python -m venv .venv` — create a local environment.
- `python -m pip install -r requirements.txt -r requirements-dev.txt` — install runtime and test dependencies.
- `docker compose up -d postgres redis qdrant` — start the data services the app depends on.
- `alembic upgrade head` — apply migrations; the app validates the schema but never runs DDL.
- `python -m services.indexer.builder` — build the Qdrant law index; add `--rebuild` to replace it.
- `uvicorn main:app --host 0.0.0.0 --port 8000 --reload --loop services.checkpoint:selector_event_loop_factory` — run the API and web UI locally. The `--loop` factory keeps psycopg's async checkpointer on a `SelectorEventLoop`, which Windows requires.
- `python run_mcp.py` — run the FastMCP server directly for inspection.
- `python -m pytest -q` — run the complete test suite.
- `cd docs && npm ci && npm run build` — verify the VitePress documentation build.

## Coding Style & Naming Conventions

Use Python 3.12-compatible code, four-space indentation, type hints, and short module docstrings. Follow `snake_case` for modules, functions, variables, and node names; use `PascalCase` for classes and Pydantic models; reserve `UPPER_CASE` for constants. Prefer async functions for network or model I/O. Keep API handlers thin. No formatter or linter is enforced, so match surrounding code.

## Testing Guidelines

Pytest and `pytest-asyncio` are configured in `pytest.ini`; async tests run in auto mode. Name files `test_<feature>.py` and tests `test_<behavior>`. Add unit tests for new services and nodes, plus regressions for routing, tool limits, citations, and state reducers. There is no coverage threshold, but every behavioral change should include a test. Document why model- or retrieval-dependent tests are skipped.

## Commit & Pull Request Guidelines

每次 Git commit 的提交信息必须使用中文，并采用简洁的祈使句主题。Keep each commit scoped and avoid committing `.env`, models, uploads, reports, or caches. Pull requests should explain the problem, implementation, affected routes/nodes, configuration changes, and exact verification commands. Link relevant issues and include screenshots for changes under `static/` or the docs site.

## Security & Agent Changes

Read secrets only from environment variables and update `.env.example` with placeholders. Before changing graph nodes, tools, storage, or MCP behavior, trace callers and preserve a runnable path. Prefer incremental changes; do not convert deterministic Router, Planner, Verifier, or formatting steps into agents without a concrete need.
