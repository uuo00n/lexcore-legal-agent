# Context Engineering 与 Memory

## 五层职责

| 层 | 数据 | 生命周期与存储 | 是否直接注入模型 |
| --- | --- | --- | --- |
| Working Memory | 当前 `AgentState`：计划、检索证据、报告、控制字段 | 当前 LangGraph thread；由 checkpoint 恢复 | 只选当前任务需要的字段 |
| Conversation Memory | `messages` | checkpoint 中的近期对话；业务消息另行归档 | 只注入最近、协议完整的有限窗口 |
| Summary Memory | 长会话滚动摘要 | `summaries` 表，按 thread 更新 | 独立预算注入 |
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

每层有独立软预算，整体还受 `CONTEXT_INPUT_TOKEN_BUDGET - CONTEXT_OUTPUT_TOKEN_RESERVE` 的硬上限约束。构造结果会记录各层估算 token、原始/注入消息数、Top-N 数量和工具摘要数量到 `context_build` trace，并写入 `context_build_status`。

## 有界策略

- 历史消息不会全量注入。默认最多 12 条，并受 recent-message token budget 双重限制。
- 超过阈值的长会话在图入口触发 compaction：旧消息生成滚动摘要后，以 `RemoveMessage` 从 checkpoint state 删除；失败时不删除原消息。
- 大 Tool observation 在送入模型前生成确定性的结构化摘要，保留状态、来源、数量和少量 Top items。collector 仍可读取原始 observation 提取证据。
- `retrieved_laws` 与 `retrieved_cases` 的 reducer 按显式 score 稳定排序、去重，并只保留配置的 Top-N；没有 score 时保持原检索顺序。
- 上传文档不再作为无限长 system message透传；Context Builder 将它作为 evidence 按预算截断。

## 配置

所有预算和 Top-N 配置见 `.env.example` 的 `Context Engineering / Memory` 小节。中文 token 目前沿用保守字符估算；如果后续模型路由暴露准确 tokenizer，可替换估算器而不改变 Context Builder 的分层契约。
