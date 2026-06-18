# OpenViking Context Layer 优化与评测记录

## 背景

OpenViking 的定位是 Agent Context Database。它不是替代 LangGraph 的多智能体编排框架，而是用文件系统范式统一管理 Agent 运行时需要的上下文：Resource、Memory、Skill，并通过 `viking://` URI、L0/L1/L2 分层加载和层级检索改善传统 flat RAG 链路。

本项目原有链路已经包含 LangGraph、MCP、Hybrid RAG、ChromaDB 长期记忆和 Trace。继续堆 Agent 的收益不高，因此本次优化选择把 OpenViking 思路作为旁路 Context Layer 接入，不推翻现有架构。

## 阶段结论

第一阶段完成的是 **本地 OpenViking-style Context Layer + A/B 评测框架**，不是完整真实 OpenViking server/SDK 接入。

第一阶段结论要分开看：

1. Context Layer 的 Resource / Skill 路由已经有价值，能把用户问题映射到法律资源目录和处理流程。
2. 直接把 `viking://` URI、L0 abstract、L1 overview 拼进 HybridRetriever query，不能证明检索提升；本轮 A/B 中检索 precision 和 recall 反而下降。
3. 因此，第一阶段不能宣称“RAG 检索能力已提升”。更准确的说法是：项目新增了 OpenViking 风格的上下文组织、Agent 路径提示和 Trace 可解释能力，并建立了后续量化验证入口。
4. 第二阶段进入“真实 OpenViking 接入 + 法律语料导入 + A/B 量化评测”。

第二阶段当前状态：

1. 项目侧真实 HTTP 适配层、法律语料导入脚本、Skill 导入脚本、`openviking_ab` 评测入口已经完成。
2. 本机已跑通 OpenViking 0.3.24 HTTP server、BGE embedding server、`find` 检索和 `openviking_ab` A/B 评测。
3. 第一轮真实 OpenViking 评测采用的是“法律名称级 Resource 卡片向量导入”，不是完整长法条全文 L0/L1/L2 导入。原因是本机 `qwen2.5:7b` 在 OpenViking 目录摘要生成阶段多次 120s timeout，导致长法条/目录语义任务卡住。
4. 第二轮已升级为“法条级 Resource 卡片 + 保守 rerank boost”。在 10 条样本、5 部核心法律的本机 A/B 中，OpenViking 组相对 baseline 提升：hit_rate +0.1000、MRR +0.1050、precision +0.0200、recall +0.1000。
5. 第三轮已完成 52 部法律、7509 张法条级 Resource 卡片的全量向量导入，并跑完 100 条全量 fast A/B。结果是 hit_rate、precision、recall 持平，MRR -0.0050，说明全量导入后直接做 OpenViking boost 不能证明检索提升。
6. 当前真实结论是：法条级 Resource 粒度是正确方向，但需要继续做领域过滤、历史版本降权、OpenViking score 融合和阈值控制，不能只靠“命中法条就 boost”。

## 主要应用阶段

OpenViking 在本项目里主要用在 **“检索前的上下文定位”和“检索后的 Agent 决策辅助”阶段**，不是用来替代现有 LangGraph Agent，也不是直接替代最终回答生成。

更准确地说，它位于用户问题进入法律检索和回答生成之前，承担 Context Router / Context Database 的角色：

```text
用户问题
→ OpenViking Context 定位
→ 判断应进入哪个法律资源目录、案件记忆、处理流程
→ 指导 HybridRetriever / MCP 法条检索 / Agent 追问策略
→ Agent 生成最终咨询回答
```

在法律咨询链路中，可以分成四个阶段：

| 阶段 | 原项目能力 | OpenViking 作用 | 是否适合重点优化 |
| --- | --- | --- | --- |
| 1. 案情理解 | LangGraph memory_node、用户画像、历史摘要 | 读取 Memory，定位历史案情、用户偏好、已有材料 | 适合 |
| 2. 上下文路由 | 原本主要靠 prompt 和检索相似度 | 用 Resource / Skill 判断问题属于劳动、合同、消费、家事、诉讼等目录 | 最适合 |
| 3. 法条检索 | HybridRetriever + MCP legal_search | 用 Resource 命中结果约束检索范围或 rerank，而不是简单拼 query | 最适合 |
| 4. 回答生成 | 主 LLM + 工具结果 + 引用法条 | 提供流程提示、证据清单、追问策略，但不能替代法条依据 | 适合但不能越界 |

因此，第二阶段的核心不是“多加一个智能体”，而是把 OpenViking 放到 **RAG 前置路由层**：

- 先用 OpenViking 的 `find/search` 从真实 `viking://resources/laws/...` 中定位法律目录。
- 再把命中的 Resource 作为 HybridRetriever 的过滤条件、候选池范围或 rerank boost。
- Skill 主要用于指导 Agent 是否追问、要收集哪些证据、下一步流程怎么走。
- Memory 主要用于多轮咨询和历史案情延续。

一句话描述：

```text
OpenViking 主要提升的是“上下文怎么被组织、定位、解释和复用”，不是直接提升“模型怎么写答案”。
```

## 原链路问题

原项目的上下文分散在不同模块：

- 法律法规：`services/retriever/hybrid.py`
- 长期记忆：`services/memory_store.py`
- 对话摘要和用户画像：`services/memory.py`
- 工具能力：`mcp_server/tools/`
- Agent 编排：`agent/graph.py`

这条链路能工作，但存在三个问题：

1. 法条、用户记忆、流程经验没有统一上下文视图。
2. RAG 主要是 flat chunk 检索，缺少面向法律领域的目录定位。
3. Trace 能看到工具调用，但不容易看到“本轮上下文从哪些资源、案件记忆、流程技能而来”。

## 优化思路

本次新增 `services/viking_context.py`，实现 OpenViking 风格的本地 Context Layer：

- Resource：法律资料目录入口，例如 `viking://resources/laws/labor/`
- Memory：案件级记忆，例如 `viking://memory/cases/{thread_id}/summary.md`
- Skill：法律处理流程，例如 `viking://skills/legal/labor_arbitration_workflow/`

每个命中上下文都包含：

- L0 abstract：短摘要，用于快速判断相关性。
- L1 overview：较详细的导航信息，用于指导 Agent 后续检索、追问和工具选择。
- `viking://` URI：用于 Trace 和后续替换真实 OpenViking server。

注意：OpenViking Context Layer 只负责上下文定位，不作为法条引用依据。明确法条引用仍必须来自本轮 MCP 法律检索工具，避免引入新的幻觉来源。

## 代码落点

新增：

- `services/viking_context.py`
- `services/openviking_client.py`
- `services/openviking_context.py`
- `services/openviking_ingest.py`
- `tests/test_viking_context.py`
- `docs/openviking-context-layer.md`
- `eval/context_ab.py`
- `eval/openviking_ab.py`
- `scripts/render_openviking_config.py`
- `scripts/start_openviking_glm47.py`
- `scripts/openviking_embedding_server.py`
- `tests/test_context_ab_eval.py`
- `tests/test_openviking_client.py`
- `tests/test_openviking_context.py`
- `tests/test_openviking_ingest.py`
- `tests/test_openviking_ab_eval.py`
- `scripts/import_openviking_corpus.py`

修改：

- `agent/state.py`：新增 `viking_context` 和 `viking_context_hits` 状态字段。
- `agent/prompts.py`：新增 `VIKING_CONTEXT_TEMPLATE`。
- `agent/nodes.py`：
  - `memory_node` 优先检索真实 OpenViking Resource / Skill，上下文不可用时回退本地 OpenViking-style Context Layer。
  - `memory_node` 记录 `viking_context_retrieval` Trace 事件。
  - `legal_consult_agent_node` 将上下文注入系统提示词。
- `services/memory_extractor.py`：对话结束后写入案件工作区。
- `eval/run_eval.py`：新增 `context_ab`、`openviking_ab` 评测模式和 `--limit` 参数。
- `eval/README.md`：补充 Context Layer A/B 和真实 OpenViking A/B 评测说明。
- `.env.example`：补充 OpenViking server 连接配置。
- `.gitignore`：忽略运行时生成的 `data/viking_context/*`，保留 `.gitkeep`。

## 新链路

```text
用户问题
→ memory_node
→ 读取用户画像、摘要、长期记忆
→ retrieve_viking_context()
→ 命中 Resource / Memory / Skill
→ 写入 viking_context 和 viking_context_hits
→ 记录 Trace: viking_context_retrieval
→ legal_consult_agent_node 注入系统提示词
→ Agent 根据上下文决定追问、检索和回答
```

对话结束后：

```text
聊天消息
→ memory_extractor
→ save_case_workspace()
→ data/viking_context/memory/cases/{thread_id}/
   ├── .abstract.md
   ├── .overview.md
   └── conversation.md
```

## 预期提升

1. 上下文组织能力提升
   从分散的 RAG、memory、tool prompt，升级为 Resource / Memory / Skill 三类上下文视图。

2. 法律咨询路径更稳定
   例如劳动纠纷会同时命中劳动法律资源、案件记忆和劳动仲裁流程 Skill，减少 Agent 只凭相似 chunk 回答。

3. 可解释性提升
   Trace 中能看到本轮命中的 `viking://` 路径，便于排查回答为什么走某个法律流程。

4. 后续可替换真实 OpenViking
   当前实现是本地确定性版本，接口已经围绕 `viking://` URI 和 L0/L1 设计。后续可将 `retrieve_viking_context()` 替换为 OpenViking SDK/MCP 的 `find/search/read`。

## 验证方式

当前新增和回归测试：

```bash
/Users/didi/Desktop/Legal/.venv/bin/pytest tests/test_viking_context.py -q
/Users/didi/Desktop/Legal/.venv/bin/pytest tests/test_context_ab_eval.py tests/test_viking_context.py -q
/Users/didi/Desktop/Legal/.venv/bin/pytest tests/test_openviking_client.py tests/test_openviking_ingest.py tests/test_openviking_ab_eval.py -q
/Users/didi/Desktop/Legal/.venv/bin/pytest tests -q
```

覆盖内容：

- 法律问题能命中 Resource / Memory / Skill 三类上下文。
- 案件工作区能写出 `.abstract.md`、`.overview.md`、`conversation.md`。
- `memory_node` 会输出 `viking_context` 和 `viking_context_hits`。
- `legal_consult_agent_node` 会把 OpenViking Context Layer 注入系统提示词。
- `context_ab` 能对同一批问题跑 baseline 和 Context Layer 两组检索。
- `disabled_query_enhancement()` 能在 smoke test 中临时关闭 HyDE/rewrite，并在结束后恢复环境变量。
- `OpenVikingHTTPClient` 按真实 HTTP API 调用 `temp_upload`、`resources`、`search/find`。
- 法律语料文件能映射到稳定的 `viking://resources/laws/{domain}/...`。
- 法律流程 Skill 能生成 OpenViking 支持的结构化 skill dict。
- `openviking_ab` 能用真实 OpenViking Resource 命中结果对 HybridRetriever 候选做 scope/rerank boost。

最近一次全量测试结果：

```text
70 passed, 6 skipped, 3 warnings
```

跳过项来自现有 RAG 初始化条件，warnings 来自现有 `pytest.mark.slow` 未注册标记，不是本次 OpenViking Context Layer 新增失败。

## A/B 评测入口

已新增离线 A/B 评测模式：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py --mode context_ab
```

快速 smoke test：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py --mode context_ab --limit 10
```

更快的框架 smoke test：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py --mode context_ab --limit 10 --fast
```

`--fast` 会临时关闭 HyDE/rewrite，只用于快速确认评测框架和上下文路由；正式对比不建议使用。

评测分组：

- A 组：原始问题直接进入现有 HybridRetriever。
- B 组：先命中 OpenViking Context Layer，再用 `viking://` URI、L0 abstract 和少量 L1 overview 扩展 query，进入同一个 HybridRetriever。

结果会保存到 `eval/results/eval_context_ab_*.json`。重点观察：

- `baseline.aggregated`
- `context_layer.aggregated`
- `delta`
- `context_routing.resource_hit_rate`
- `context_routing.skill_hit_rate`

## 本轮 A/B 最终指标

本轮正式命令：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py --mode context_ab --limit 10 --top-k 5
```

结果文件：

```text
eval/results/eval_context_ab_20260615_141908.json
```

评测配置：

```text
mode: context_ab
top_k: 5
num_queries: 10
num_total_queries: 10
fast: false
```

检索指标：

| 指标 | Baseline: raw query | Context Layer query | Delta |
| --- | ---: | ---: | ---: |
| hit_rate | 0.6000 | 0.6000 | 0.0000 |
| mrr | 0.4200 | 0.4083 | -0.0117 |
| precision | 0.2433 | 0.1717 | -0.0717 |
| recall | 0.4667 | 0.4167 | -0.0500 |

上下文路由指标：

| 指标 | 结果 |
| --- | ---: |
| resource_hit_rate | 0.7000 |
| skill_hit_rate | 0.8333 |
| avg_context_hits | 1.5000 |
| avg_viking_prompt_chars | 408.0000 |
| avg_context_query_chars | 271.0000 |
| resource_eval_count | 10 |
| skill_eval_count | 6 |

## 指标解读

这次 A/B 的结果不是单纯的“优化成功”，而是暴露出一个很关键的问题：

1. `resource_hit_rate = 0.7`，说明 Resource 目录定位有一定效果，但还有明显误路由。
2. `skill_hit_rate = 0.8333`，说明 Skill 流程定位比 Resource 更稳定，适合用于 Agent 追问、证据清单和处理路径规划。
3. 检索 `hit_rate` 持平，但 `mrr`、`precision`、`recall` 下降，说明直接扩展 query 会引入噪声。
4. 当前 B 组只是“把上下文目录提示拼进 query”，不是 OpenViking 真正的层级资源检索，也没有把 Resource 变成检索过滤、候选池约束或 rerank boost。

具体坏例子：

- “公司三年不续签合同”被“合同”关键词带偏，命中了合同审查 Skill 和民法典合同编 Resource，没有命中劳动争议目录。
- “网购买衣服不喜欢能否退货”命中了消费者权益 Resource，但扩展 query 后反而没有召回原 baseline 命中的 `消费者权益保护法_第二十五条`。
- “遛狗没牵绳被车撞”命中了合同审查 Skill，属于明显误路由。

因此，本阶段的真实结论是：

```text
OpenViking-style Context Layer 对 Agent 上下文组织和流程路由有价值；
但“拼接上下文扩展检索 query”不是合格的最终检索增强方案。
```

## 当前能力边界

已经完成：

- 本地 OpenViking-style Context Layer。
- 真实 OpenViking HTTP 适配层，不直接依赖 OpenViking Python 包。
- 本机 OpenViking 0.3.24 server 启动和连通性验证。
- 本机 BGE embedding server 接入 OpenViking OpenAI-compatible embedding 接口。
- 法律语料 Resource 导入脚本。
- 法律流程 Skill 导入脚本。
- 法律资源卡片向量导入，用于绕开本地小模型摘要超时并验证真实 OpenViking 检索链路。
- Resource / Memory / Skill 三类上下文抽象。
- `viking://` URI 和 L0/L1 上下文提示。
- 案件级 Memory 工作区写入。
- LangGraph 状态注入。
- Agent system prompt 注入。
- Trace 事件记录。
- Context Layer A/B 评测框架。
- 真实 OpenViking `openviking_ab` 评测入口。
- 第一轮正式 A/B 指标。
- 第一轮法律名称级真实 OpenViking A/B 指标。
- 第二轮法条级真实 OpenViking A/B 指标。
- 52 部法律、7509 张法条级 Resource 卡片的全量向量导入。
- 100 条数据集的全量 fast A/B 指标。

尚未完成：

- 完整长法条全文在真实 OpenViking server 中稳定导入。
- 带 HyDE/rewrite 的真实 OpenViking 全量慢链路评测。
- 完整法律语料的 L0/L1/L2 层级摘要和 RAGFS 索引构建。
- 法律流程 Skill 在真实 OpenViking server 中的完整稳定导入和评测。
- OpenViking MCP / LangGraph adapter 替换当前本地 adapter。
- 端到端回答质量 A/B。
- 法条引用准确率、人类可读性、追问率等业务指标验证。

## 真实 OpenViking A/B 运行记录

本轮运行日期：2026-06-15

运行环境：

```text
OpenViking: 0.3.24
HTTP server: http://localhost:1933
Embedding: bge-small-zh-v1.5, 512 dim, OpenAI-compatible local server
LLM: Ollama qwen2.5:7b / qwen2.5:1.5b
Workspace: /tmp/openviking-legal-direct-data
Vector count: 27
```

导入方式：

```text
9 张法律 Resource 卡片
→ viking://resources/laws/...
→ 直接写入 OpenViking resource context 向量
→ level=2 detail 检索
```

本轮没有宣称完成“全量法律语料导入”。实际尝试过 `add_resource` 导入长法条文件，但 OpenViking 会触发目录摘要生成；本机 Ollama 7B 多次超时，并出现 `.path.ovlock` 长时间占用。因此本轮为了先完成可量化闭环，改为资源卡片向量导入。

正式命令：

```bash
OPENVIKING_BASE_URL=http://localhost:1933 OPENVIKING_TIMEOUT=120 \
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py \
  --mode openviking_ab --limit 10 --top-k 5 --openviking-limit 5
```

结果文件：

```text
eval/results/eval_openviking_ab_20260615_175054.json
```

核心指标：

| 指标 | Baseline | OpenViking Resource rerank | Delta |
| --- | ---: | ---: | ---: |
| hit_rate | 0.6000 | 0.6000 | 0.0000 |
| mrr | 0.4700 | 0.4700 | 0.0000 |
| precision | 0.2433 | 0.2350 | -0.0083 |
| recall | 0.4667 | 0.4667 | 0.0000 |

OpenViking 路由指标：

| 指标 | 结果 |
| --- | ---: |
| resource_hit_rate | 0.6000 |
| avg_openviking_matches | 5.0000 |
| resource_eval_count | 10 |

结论：

1. 真实 OpenViking server、embedding、Resource `find`、项目侧 A/B 评测已经跑通。
2. Resource 卡片能稳定把劳动、消费、租赁、婚姻家事、交通等问题路由到对应法律资源。
3. 只把 OpenViking 命中的法律名称用于 rerank boost，最终 top-k 检索指标基本持平；precision 轻微下降。
4. 下一轮提升重点不应是继续堆 Agent，而是完善法律资源粒度：把 Resource 从“法律名称卡片”升级为“法条/章节/案例/模板分层节点”，再让 OpenViking 命中结果约束候选池。

## 第二阶段实现入口

第二阶段目标是“真实 OpenViking 接入 + 法律语料导入 + A/B 量化评测”。当前已经完成真实 server 闭环，但完整语料导入仍需继续优化。

环境变量：

```bash
OPENVIKING_BASE_URL=http://localhost:1933
OPENVIKING_API_KEY=your-openviking-key
```

导入法律法规 Resource：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python scripts/import_openviking_corpus.py --laws --wait
```

导入法条级 Resource 卡片：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python scripts/import_openviking_corpus.py --article-cards --wait --write-mode replace
```

默认会在写入后触发一次 `viking://resources/laws` 的 `vectors_only` 重建；只想写入不重建时加 `--no-build-index`。

只导入部分领域用于 smoke test：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python scripts/import_openviking_corpus.py --laws --domains labor,consumer_protection --wait
```

导入法律流程 Skill：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python scripts/import_openviking_corpus.py --skills --wait
```

真实 OpenViking A/B：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py --mode openviking_ab --limit 10 --top-k 5
```

全量 fast A/B：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py --mode openviking_ab --top-k 5 --fast
```

`--fast` 会临时关闭 HyDE/rewrite。它适合看 OpenViking Resource rerank 对检索排序的净影响；不能等同于带 HyDE/rewrite 的正式慢链路。

这一版 B 组不再把上下文拼进 query，而是：

```text
用户问题
→ OpenViking find(query, target_uri="viking://resources/laws", context_type="resource")
→ 得到法律/法条 Resource URI
→ 原 HybridRetriever 召回更大的候选池
→ 按 OpenViking 命中的法条精确 ID 和法律资源做保守 rerank boost
→ 计算 hit_rate / MRR / precision / recall
```

## 法条级 Resource 优化记录

本轮运行日期：2026-06-16

改动：

1. 新增法条级 OpenViking Resource 卡片构建：
   - 每个 `LawChunk` 生成一个 URI，例如 `viking://resources/laws/labor/劳动合同法/劳动合同法_第二十条.md`。
   - 卡片内容包含 `law_name`、`article_no`、`chunk_id`、`domain`、`hierarchy` 和法条正文。
2. `scripts/import_openviking_corpus.py` 新增 `--article-cards`，用于导入法条级轻量 Resource。
3. `openviking_ab` 从“法律名称级 boost”升级为“法条 ID 精确 boost + 法律名称兜底 boost”。
4. 第一版 hard rerank 会被 OpenViking 相近错条带偏，例如“试用期工资”把 `劳动合同法_第八十三条` 排到 `第二十条` 前面；因此改成保守 boost，让原 Hybrid/reranker 排名仍是主排序。

运行环境：

```text
OpenViking: 0.3.24
Embedding: 本地 bge-small-zh-v1.5, 512 dim
Workspace: /tmp/openviking-legal-articles-data-20260616
导入范围: 民法典、劳动合同法、劳动法、消费者权益保护法、民事诉讼法
导入法条卡片: 1649
Vector count: 1667
```

正式命令：

```bash
OPENVIKING_BASE_URL=http://localhost:1933 OPENVIKING_TIMEOUT=120 \
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py \
  --mode openviking_ab --limit 10 --top-k 5
```

结果文件：

```text
eval/results/eval_openviking_ab_20260616_163041.json
```

核心指标：

| 指标 | Baseline | OpenViking article boost | Delta |
| --- | ---: | ---: | ---: |
| hit_rate | 0.5000 | 0.6000 | +0.1000 |
| mrr | 0.3200 | 0.4250 | +0.1050 |
| precision | 0.2233 | 0.2433 | +0.0200 |
| recall | 0.3667 | 0.4667 | +0.1000 |

OpenViking 路由指标：

| 指标 | 结果 |
| --- | ---: |
| resource_hit_rate | 0.6000 |
| avg_openviking_matches | 20.0000 |
| resource_eval_count | 10 |

结论：

1. 法条级 Resource 比法律名称级 Resource 更适合本项目的法条 ID 评测。
2. 硬 rerank 不可用，会被相近错条伤害 MRR；保守 boost 更稳。
3. 本轮结果说明“法条级 OpenViking + 保守 rerank”在同一次 A/B 中带来可量化提升，但导入范围仍只覆盖前 10 条评测样本涉及的核心法律，不代表全量法律语料效果。
4. 下一步应继续做全量法条级导入、分领域阈值和 OpenViking match score 融合，而不是只换 embedding 模型。

## 全量法条级 Resource A/B 记录

本轮运行日期：2026-06-16

运行环境：

```text
OpenViking: 0.3.24
OpenViking runner: /tmp/openviking-runner
HTTP server: http://127.0.0.1:1933
Embedding: 本地 bge-small-zh-v1.5, 512 dim
Workspace: /tmp/openviking-legal-full-data-20260616
导入范围: data/laws 下 52 部法律
导入法条卡片: 7509
Vector count: 7658
Embedding queue: 7677 processed, 0 pending, 0 errors
```

导入说明：

1. 全新 workspace 中使用 `content/write` 的 `replace` 模式会失败，因为目标法条文件尚不存在。
2. 改用 `create` 模式可以写入法条卡片，但 OpenViking 仍会为目录和节点排 Semantic / Semantic-Nodes 任务。
3. 本机 Ollama `qwen2.5:7b` 对这些摘要任务仍存在 timeout；因此本轮只以向量入库完成作为评测前置条件，不等待完整 L0/L1/L2 摘要任务全部完成。
4. 这说明“法条级轻量 Resource + vectors_only”适合当前本机验证；完整 OpenViking RAGFS 语义层仍需要更稳定的摘要模型或关闭/替换摘要生成策略。

全量 fast A/B 命令：

```bash
OPENVIKING_BASE_URL=http://127.0.0.1:1933 OPENVIKING_TIMEOUT=120 \
/Users/didi/Desktop/Legal/.venv/bin/python eval/run_eval.py \
  --mode openviking_ab --top-k 5 --fast
```

结果文件：

```text
eval/results/eval_openviking_ab_20260616_173149.json
```

评测配置：

```text
mode: openviking_ab
fast: true
top_k: 5
num_queries: 100
openviking_limit: 20
```

核心指标：

| 指标 | Baseline | OpenViking article boost | Delta |
| --- | ---: | ---: | ---: |
| hit_rate | 0.5700 | 0.5700 | 0.0000 |
| mrr | 0.4313 | 0.4263 | -0.0050 |
| precision | 0.1295 | 0.1295 | 0.0000 |
| recall | 0.5467 | 0.5467 | 0.0000 |

OpenViking 路由指标：

| 指标 | 结果 |
| --- | ---: |
| resource_hit_rate | 0.5714 |
| avg_openviking_matches | 20.0000 |
| resource_eval_count | 70 |

差异分析：

```text
reciprocal_rank: win 0 / loss 1 / tie 99
hit: win 0 / loss 0 / tie 100
```

唯一 MRR 下降样例：

```text
问题: 对方一直不履行合同，我能不能直接解除合同
Baseline top1: 民法典_第五百六十三条
OpenViking boost 后 top1: 合同法_历史版本_第九十四条
```

原因是 OpenViking 全量库命中了 `合同法_历史版本`，而评测 ground truth 更偏向现行 `民法典_第五百六十三条`。这说明全量导入后必须处理“历史版本法律”和“现行法律”的优先级，不能把所有命中的法条一视同仁。

本轮结论：

1. 全量法条级 Resource 向量导入已跑通，真实 OpenViking find 可以在 52 部法律、7509 张法条卡片上工作。
2. 在关闭 HyDE/rewrite 的 100 条全量 A/B 中，OpenViking article boost 没有提升整体检索指标，MRR 轻微下降。
3. 小样本提升不代表全量稳定提升；全量后噪声主要来自跨领域相似法条、历史版本法律、OpenViking top match 近似错条。
4. 下一步优化重点应从“是否接入 OpenViking”转为“怎样约束 OpenViking 命中”：领域 filter、历史版本降权、score 阈值、exact article boost 权重、law-level fallback 权重。

## 完整 OpenViking 语义层接入记录

本轮目标：

1. 不再依赖 `/tmp/openviking-legal-conf` 下的一次性 qwen 实验配置。
2. 用 GLM-4.7 替换 OpenViking 语义摘要/概览生成里的本地 `ollama/qwen2.5:7b`。
3. 启用完整 L0/L1/L2 语义层，让 Resource / Skill 能通过 `find/search/read/abstract/overview` 被主 Agent 使用。
4. 让 `memory_node` 优先接真实 OpenViking Resource / Skill，失败时回退本地 Context Layer。

新增运行入口：

```bash
# 生成 GLM-4.7 OpenViking 配置，输出到 .runtime/openviking/ov_glm47.conf
/Users/didi/Desktop/Legal/.venv/bin/python scripts/render_openviking_config.py --show

# 启动本地 embedding endpoint 和 OpenViking server
/Users/didi/Desktop/Legal/.venv/bin/python scripts/start_openviking_glm47.py

# 完整语义层导入：article Resource + Skill + semantic_and_vectors reindex
/Users/didi/Desktop/Legal/.venv/bin/python scripts/import_openviking_corpus.py \
  --article-cards --skills --write-mode upsert --no-write-wait \
  --reindex-mode semantic_and_vectors --wait-after-import --wait-timeout 600
```

关键配置：

```text
OPENVIKING_GLM_MODEL=glm-4.7
OPENVIKING_GLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
OPENVIKING_CONTEXT_ENABLED=true
OPENVIKING_CONTEXT_TIMEOUT=3
OPENVIKING_RESOURCE_TARGET_URI=viking://resources/laws
OPENVIKING_SKILL_TARGET_URI=
```

配置生成规则：

1. `scripts/render_openviking_config.py` 默认从 `OPENVIKING_GLM_API_KEY` 读取 OpenViking 专用 GLM key；如果没有，则复用主项目的 `ZHIPU_API_KEY`。
2. 生成配置默认启用 `auto_generate_l0=true`、`auto_generate_l1=true`、`default_search_mode=thinking`。
3. 生成配置默认使用本地 BGE embedding endpoint：`http://localhost:11435/v1`，模型为 `bge-small-zh-v1.5`，维度 512。
4. GLM 语义生成默认 `OPENVIKING_GLM_MAX_CONCURRENT=1`，避免本机批量导入时触发 GLM-4.7 速率限制；如果账户限流更高，可以再手动调大。
5. Query planner 默认 `OPENVIKING_QUERY_PLANNER_MAX_CONCURRENT=1`，避免 `thinking/search` 阶段再次触发 GLM 429。
6. 运行时配置写入 `.runtime/openviking/ov_glm47.conf`，`.runtime/` 已加入 `.gitignore`，避免提交密钥。

主 Agent 链路变化：

```text
memory_node
→ services.openviking_context.retrieve_agent_context()
→ 真实 OpenViking find(Resource)
→ 真实 OpenViking find(Skill)
→ 用 Resource domain 过滤 Skill 噪声
→ 注入 viking_context / viking_context_hits
→ legal_consult_agent 系统提示词使用 Resource / Skill 做路由和检索策略提示
```

安全边界：

1. OpenViking Resource / Skill 只作为上下文定位、检索范围和流程提示。
2. 明确法条引用仍必须来自本轮法律检索工具结果，不能只依据 L0/L1 摘要。
3. OpenViking 服务不可用、超时或无命中时，`memory_node` 会自动回退到本地 OpenViking-style Context Layer，不阻塞主咨询链路。

### 2026-06-17 完整 OpenViking 接入验收

本轮完成两层目标：

1. 完整 OpenViking server + GLM-4.7 + L0/L1/L2 跑通。
2. `memory_node` 接真实 OpenViking Resource / Skill，并在 Agent 上下文中注入。

工程改动：

1. `scripts/start_openviking_glm47.py` 改为可靠启动脚本：先启动本地 embedding endpoint，等待 `/health`；再启动 OpenViking server，等待 `/ready`；子进程使用独立 process group，避免脚本退出后被外层 shell 清理。
2. `scripts/render_openviking_config.py` 统一使用 `glm-4.7`，`vlm.max_concurrent=1`，`query_planner.max_concurrent=1`，并保留环境变量覆盖。
3. `services.openviking_context.retrieve_agent_context()` 优先真实 OpenViking `find(Resource)` + `find(Skill)`，失败时回退本地 Context Layer。
4. `services.openviking_context` 增加 Skill domain 过滤：先从 Resource URI 解析法律领域，例如 `viking://resources/laws/labor/...`，再只注入同领域 Skill；避免劳动问题混入押金、合同审查等无关流程。

启动验证：

```bash
/Users/didi/Desktop/Legal/.venv/bin/python scripts/start_openviking_glm47.py --startup-timeout 240
```

结果：

```text
Embedding ready: http://127.0.0.1:11435/health
OpenViking ready: http://127.0.0.1:1933/ready
OpenViking version: 0.3.24
ready checks: agfs ok, vectordb ok, embedding ok, ollama not_configured
```

配置验证：

```text
OpenViking semantic model: glm-4.7
embedding model: bge-small-zh-v1.5
auto_generate_l0: true
auto_generate_l1: true
default_search_mode: thinking
vlm.max_concurrent: 1
query_planner.max_concurrent: 1
```

Resource 链路 smoke：

```text
query: 试用期被公司辞退 能申请劳动仲裁吗
resource_hits: 5
L2 read: viking://resources/laws/labor/劳动争议调解仲裁法/劳动争议调解仲裁法_第一条.md
L0 abstract: viking://resources/laws/labor/劳动法
L1 overview: viking://resources/laws/labor/劳动法
```

说明：`find` 能返回 L2 法条卡片、L1 目录 overview、L0 abstract；`read()` 能读取法条原文；目录级 `abstract()` / `overview()` 能返回中文摘要和导航。

Skill 链路 smoke：

```text
query: 劳动仲裁 试用期 辞退 证据 流程
skill_hits: 5
top skill: viking://agent/default/skills/labor-arbitration-workflow/.abstract.md
```

`memory_node` 真实注入 smoke：

```text
query: 试用期被公司辞退，能申请劳动仲裁吗？
hits: 5
resource_hits: 4
skill_hits: 1
skill: viking://agent/default/skills/labor-arbitration-workflow/.abstract.md
```

这说明主 Agent 链路已经不是“OpenViking-style 本地模拟层”，而是：

```text
用户问题
→ memory_node
→ 真实 OpenViking Resource find
→ 真实 OpenViking Skill find
→ Resource domain 约束 Skill
→ 注入 viking_context / viking_context_hits
→ 后续 Agent 依据 Resource / Skill 做流程和检索策略判断
```

仍需保留的边界：

1. GLM-4.7 确实已经接入 OpenViking 语义链路；此前导入 Skill/语义重建时触发过智谱 429，说明请求真实打到了 GLM，但账户限流仍可能影响全量重建。
2. 当前 smoke 证明 server、embedding、Resource L0/L1/L2、Skill find、`memory_node` 注入已跑通；如果要重新全量导入 7500+ 法条并等待所有 L0/L1 重建，仍建议避开限流窗口或使用更高限额 key。
3. OpenViking 上下文仍只用于领域定位、流程提示和检索策略；最终法条引用必须由本轮法律检索工具确认。

### 第二阶段后续步骤

1. 跑通真实 OpenViking server/SDK 或 MCP adapter，确认本地项目能通过 OpenViking API 读写 context。
2. 把法律法规、合同模板、案例样例导入为 Resource，形成真实 L0/L1/L2 层级。
3. 把劳动仲裁、押金纠纷、合同审查、证据收集、诉讼时效、起诉流程导入为 Skill。
4. 把用户案情和历史咨询写入 Memory，并明确隐私和清理策略。
5. 改造检索增强方式：不要再简单拼 query，而是用 Resource 命中结果做 domain filter、candidate pool 限定或 rerank boost。
6. 针对全量 A/B 暴露的问题继续优化：
   - 历史版本法律降权或默认排除，例如 `合同法_历史版本`、`物权法_历史版本`。
   - 根据问题和 baseline 候选推断 domain，只允许 OpenViking 在同 domain 内 boost。
   - 引入 OpenViking score 阈值，低置信命中只做解释，不参与排序。
   - 区分 exact article boost 和 law-level boost，进一步降低 law-level boost 权重。
7. 重跑 A/B：
   - A：原 HybridRetriever。
   - B1：当前本地 Context Layer query augmentation。
   - B2：真实 OpenViking Resource-scoped retrieval / rerank。
   - B3：真实 OpenViking + 端到端 Agent 回答。
8. 设定通过标准：
   - 本地 Context Layer 的 Resource 路由命中率明显高于当前 0.7000。
   - 真实 OpenViking 全量 Resource 命中率明显高于当前 0.5714。
   - Skill 路由命中率不低于当前 0.8333。
   - 检索 hit_rate、MRR、precision、recall 至少不能低于 baseline。
   - 端到端回答中法条引用准确率、事实不足追问率和证据清单完整度有可解释提升。

## 后续补充区

后续继续补充时建议按这个格式追加：

```text
日期：
阶段：
改动：
运行命令：
结果文件：
核心指标：
结论：
下一步：
```
