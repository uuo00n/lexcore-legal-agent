# Context Engineering 与 Memory

## 五层职责

| 层 | 数据 | 生命周期与存储 | 是否直接注入模型 |
| --- | --- | --- | --- |
| Working Memory | 当前 `AgentState`：计划、检索证据、报告、控制字段 | 当前 LangGraph thread；由 checkpoint 恢复 | 只选当前任务需要的字段 |
| Conversation Memory | `messages` | checkpoint 中的近期对话；业务消息另行归档 | 只注入最近、协议完整的有限窗口 |
| Summary Memory | 长会话滚动摘要 | `conversation_summaries` 表，按 thread 更新 | 独立预算注入 |
| Long-term Memory | 用户稳定身份、偏好、持续事项和明确要求记住的信息 | 独立 Memory Store；按 `user_id` namespace，无用户身份时退化为 thread 隔离 | 仅语义检索命中的 Top-K |
| Persistent Workflow State | PostgreSQL `AsyncPostgresSaver` checkpoint | 保存可恢复的图状态、节点进度与近期消息 | 不等于长期用户记忆 |

长期 Memory 与 checkpoint 的目的不同。checkpoint 解决中断恢复和线程连续性，可以被压缩、过期或删除；长期 Memory 只保存值得跨轮复用的用户相关信息，并必须做用户 namespace 隔离。不能从 checkpoint 的存在推断某条信息值得长期保存，也不能把 Memory Store 当作工作流恢复机制。

## 每次模型调用

`services.context_builder.build_model_context` 是 Specialist 模型输入的统一入口，按以下顺序构造：

1. 基础 system prompt；
2. relevant memory（画像、相关长期记忆、OpenViking 命中）；
3. conversation summary；
4. current plan；
5. retrieved evidence（法规、案例、专家报告、上传材料）；
6. current task；
7. recent messages。

每层有独立软预算，整体还受 `CONTEXT_INPUT_TOKEN_BUDGET - CONTEXT_OUTPUT_TOKEN_RESERVE` 的硬上限约束。构造结果会记录档位、各层估算 token、原始/注入消息数、Top-N 数量和工具摘要数量到 `context_build` trace，并写入 `context_build_status`。

## 上下文档位

预算不是一个固定值，而是按本轮实际需要分档。档位由 `build_model_context` 在调用前确定性定档（不调用模型），写入 `context_build` trace 的 `context_tier` / `tier_reason` / `tier_signals`：

| 档位 | 适用场景 | 输入预算 | 输出预留 | 目标使用量 | 法条 / 类案 Top-N | 近期消息 |
| --- | --- | --- | --- | --- | --- | --- |
| `standard` | 普通法律问答 | 32K | 8K | 16K～32K | 6 / 4 | 12 |
| `complex` | 复杂案件分析（默认档） | 64K | 12K | 32K～64K | 10 / 6 | 19 |
| `long` | 长合同、多份证据、大量类案 | 128K | 16K | 64K～128K | 15 / 10 | 30 |

定档规则，从严到宽：

1. 材料 token 数（上传文档 + 视频证据摘要 + 检索到的法条/类案/专家报告正文）达到 `CONTEXT_LONG_MATERIAL_TOKENS`，或类案条数达到 `CONTEXT_LONG_CASE_COUNT` → `long`；
2. Complexity Router 定档为 `complex`（或旧字段 `task_complexity=high`）→ `complex`；
3. 定档为 `simple` / `medium` → `standard`；
4. 路由还没跑（例如事实分析之前）→ 默认档 `complex`，即「单次任务默认上下文预算 64K」。

三档都由 `CONTEXT_INPUT_TOKEN_BUDGET` 与 `CONTEXT_OUTPUT_TOKEN_RESERVE` 按比例推导（输入 ×0.5 / ×1 / ×2，预留 ×1 / ×1.5 / ×2，条数 ×1 / ×1.6 / ×2.5），并统一被 `CONTEXT_MODEL_MAX_TOKENS` 夹住，因此只调这两个基准就能整体缩放，不会出现各档口径互相矛盾。单档位可用 `CONTEXT_TIER_<STANDARD|COMPLEX|LONG>_*` 覆盖。

窗口放大必须同时放大条数：Top-N 与近期消息条数比 token 更早成为瓶颈，只提 token 预算等于空转。因此工作态 `retrieved_laws` / `retrieved_cases` 的保留上限取最大档位（法条 15 条、类案 10 条），真正送进模型的条数再按本轮档位挑选。

## 有界策略

- 历史消息不会全量注入。条数上限随档位放大（12 / 19 / 30 条），并受 recent-message token budget 双重限制。
- 超过阈值的长会话在图入口触发 compaction：旧消息生成滚动摘要后，以 `RemoveMessage` 从 checkpoint state 删除；失败时不删除原消息。压缩保留的最近消息条数默认对齐最大档位（`CONTEXT_COMPACT_KEEP_RECENT=30`），否则长上下文档要读的对话会在压缩阶段先被删掉。
- 大 Tool observation 在送入模型前生成确定性的结构化摘要，保留状态、来源、数量和少量 Top items。collector 仍可读取原始 observation 提取证据。
- `retrieved_laws` 与 `retrieved_cases` 的 reducer 按显式 score 稳定排序、去重，并只保留最大档位的 Top-N；没有 score 时保持原检索顺序。
- 上传文档不再作为无限长 system message透传；Context Builder 将它作为 evidence 按预算截断，Specialist 不再各自裁剪。

## 配置

所有预算、档位阈值和 Top-N 配置见 `.env.example` 的 `Context Engineering / Memory` 小节。各层软预算默认按 prompt 预算比例分配（system 9% / memory 8% / summary 6% / recent 24% / plan 6% / evidence 37% / task 10% / tool 2%）；`CONTEXT_*_TOKEN_BUDGET` 一旦显式设置就是绝对值覆盖，会对所有档位生效并使档位缩放失效。中文 token 目前沿用保守字符估算；如果后续模型路由暴露准确 tokenizer，可替换估算器而不改变 Context Builder 的分层契约。

输出预留只表示「prompt 不得占用这部分窗口」，当前不会被写成 provider 的 `max_tokens` 参数。
