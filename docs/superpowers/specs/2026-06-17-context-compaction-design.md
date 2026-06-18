# Context Compaction Design

## Goal

Add Claude/Codex-style context compaction to the Legal agent so long conversations stay within the model context budget while preserving case facts and entity memory.

## Current State

The project already has a memory system:

- `services.memory` stores archived messages, rolling summaries, and user profiles.
- `services.memory_extractor` runs after a chat response and updates summaries, long-term memories, and profiles.
- `agent.nodes.legal_consult_agent_node` sends only the latest `SLIDING_WINDOW_SIZE` messages to the model.

The missing piece is runtime compaction. Old messages remain in the LangGraph checkpoint, and users cannot see context usage or manually compact.

## Design

Add a pre-flight LangGraph node named `context_compaction`. It runs before `memory` on every request. It estimates current context usage, checks whether the checkpoint is over budget, and when needed:

1. Keeps the latest `SLIDING_WINDOW_SIZE` messages as raw conversational context.
2. Sends older compactable messages to a lightweight memory model.
3. Parses a structured compaction result containing `summary`, `entities`, `case_profile`, `open_questions`, and `legal_focus`.
4. Merges the summary into SQLite `summaries`.
5. Merges stable identity/preference data into `user_profiles`.
6. Stores case-specific facts in the profile under `case_profile` so legal case facts do not overwrite stable user identity.
7. Returns `RemoveMessage` updates for compacted messages, leaving the checkpoint small.

The node degrades safely. If the LLM or database fails, it records no deletion and lets the existing sliding-window prompt path continue.

## User Controls

Expose two API endpoints:

- `GET /api/threads/{thread_id}/context` returns message count, estimated tokens, budget, usage ratio, and whether auto compaction is recommended.
- `POST /api/threads/{thread_id}/compact` manually compacts the current checkpoint and returns the updated context status.

The web app shows a compact context meter near the provider label and provides a small "压缩" button. The meter refreshes on thread switch and after every chat turn. Manual compaction is disabled while a chat request is streaming.

## Data Model

No new database table is required for the first version. The existing `summaries` table stores the rolling thread summary. Existing `user_profiles` stores a conservative merged object:

```json
{
  "identity": "员工",
  "focus_areas": ["劳动纠纷"],
  "preferences": ["先看风险"],
  "case_profile": {
    "parties": ["公司", "用户"],
    "facts": ["用户工作三年"],
    "dates": [],
    "amounts": [],
    "documents": [],
    "open_questions": [],
    "legal_focus": ["经济补偿"]
  }
}
```

Empty fields never overwrite existing values. Lists are appended and de-duplicated.

## Configuration

Use environment defaults:

- `CONTEXT_WINDOW_TOKEN_BUDGET=12000`
- `CONTEXT_AUTO_COMPACT_RATIO=0.75`
- `CONTEXT_AUTO_COMPACT_MESSAGES=16`
- `CONTEXT_COMPACTION_MODEL` defaults to `MEMORY_EXTRACTOR_MODEL` or `glm-4.5-air`

## Testing

Tests cover:

- Token and message usage estimation.
- Auto-compaction threshold decisions.
- Structured compaction parsing and fallback.
- Entity memory merge behavior.
- `RemoveMessage` output for compacted checkpoint messages.
- Context status API.
- Manual compaction API.
- SSE `context_status` event so the frontend can update the meter.
