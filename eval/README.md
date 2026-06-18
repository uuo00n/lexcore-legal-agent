# RAGAS 自动评测系统 — 实施计划

## Context

项目 RAG + Agent 功能已完成，需要建立自动化评测体系来量化检索质量和回答质量。使用 RAGAS 框架，评测数据集包含 100 条法律场景案例。

评测分两层：
1. **检索评测** — 法条是否检索对了（context precision/recall）
2. **端到端评测** — 最终回答是否正确、是否忠实于检索到的法条（faithfulness/correctness）

## 整体方案

### 1. 测试数据集格式

`eval/dataset.json`，每条数据包含：

```json
{
  "question": "房东不退押金怎么办？",
  "ground_truth": "承租人可以依据民法典要求房东返还押金...",
  "ground_truth_contexts": [
    "民法典_第七百一十四条",
    "民法典_第五百七十七条"
  ],
  "acceptable_contexts": [
    "民法典_第七百一十四条",
    "民法典_第五百七十七条"
  ],
  "corpus_status": "in_corpus"
}
```

字段说明：
- `question`: 用户问题（口语化法律场景）
- `ground_truth`: 标准答案（期望的回答要点）
- `ground_truth_contexts`: 相关法条的 chunk_id 列表（格式：`{law_name}_{article_no}`，与 `LawChunk.chunk_id` 对应）
- `acceptable_contexts`: 检索评测可接受命中的 chunk_id。用于“标准法条不是唯一合理答案”的场景
- `corpus_status`: `in_corpus` 或 `out_of_corpus`。后者会在本地 RAG 汇总指标中跳过，避免把语料缺失算成检索失败

### 2. ChatGPT 生成 Prompt

`eval/generate_prompt.md` 提供精心设计的 prompt，用户复制到 ChatGPT 可生成候选案例。写入 `dataset.json` 前必须用本地 chunk 列表校验所有 `ground_truth_contexts` 和 `acceptable_contexts`。

### 3. 评测脚本

**A. 检索评测（不需要 LLM judge）**

直接调用 `HybridRetriever.retrieve()`，对比返回的 chunk_id 与 ground_truth_contexts：
- **Hit Rate**: 至少命中一条相关法条的比例
- **MRR**: 第一条相关法条的排名倒数
- **Context Recall**: 检索到的相关法条数 / 标准答案中的法条总数
- **Context Precision**: 检索到的相关法条数 / 检索返回的总数

**B. 端到端评测（需要 LLM judge）**

调用完整的 LangGraph Agent，获取最终回答，用 RAGAS LLM-as-judge 评估：
- **Faithfulness**: 回答是否忠实于检索到的上下文
- **Answer Relevancy**: 回答是否切题
- **Answer Correctness**: 回答与标准答案的语义一致性

### 4. 文件结构

```
eval/
├── README.md               # 本文件
├── generate_prompt.md      # 给 ChatGPT 的生成 prompt
├── dataset.json            # 100 条测试数据
├── run_eval.py             # 评测主脚本
├── metrics.py              # 指标计算逻辑
└── results/                # 评测结果输出目录
```

### 5. 运行方式

```bash
# 仅检索评测（快速，不需要 LLM judge）
python eval/run_eval.py --mode retrieval

# OpenViking Context Layer A/B 评测（快速，不需要 LLM judge）
python eval/run_eval.py --mode context_ab

# 只跑前 10 条，适合 smoke test
python eval/run_eval.py --mode context_ab --limit 10

# 更快的 smoke test：临时关闭 HyDE/rewrite，仅验证 A/B 框架和上下文路由
python eval/run_eval.py --mode context_ab --limit 10 --fast

# 真实 OpenViking A/B 评测（需要先启动 OpenViking server 并导入语料）
python eval/run_eval.py --mode openviking_ab --limit 10 --top-k 5

# 端到端评测（需要 MCP Server + RAGAS + 已配置的主 LLM API Key）
python eval/run_eval.py --mode e2e

# 全部评测
python eval/run_eval.py --mode all
```

## 6. OpenViking Context Layer A/B 评测

`context_ab` 模式用于验证 OpenViking 风格 Context Layer 是否真的改善上下文链路。

### A/B 分组

- **A 组 baseline**：原始用户问题直接进入 `HybridRetriever.retrieve()`。
- **B 组 context_layer**：先调用 `services.viking_context.retrieve_viking_context()`，命中 Resource / Memory / Skill 后，将 `viking://` URI、L0 abstract 和少量 L1 overview 作为 query planning context，再进入同一个 `HybridRetriever.retrieve()`。

### 指标

检索指标复用原有 Retrieval Metrics：

- `hit_rate`：是否命中任一可接受法条。
- `mrr`：第一条相关法条的排名倒数。
- `precision`：返回结果中相关法条占比。
- `recall`：可接受法条中被召回的比例。

新增上下文路由指标：

- `resource_hit_rate`：Context Layer 是否命中预期法律资源目录。
- `skill_hit_rate`：Context Layer 是否命中预期法律处理流程。
- `avg_context_hits`：平均每题命中的 Resource / Memory / Skill 数量。
- `avg_viking_prompt_chars`：注入 Agent 的上下文字符数。
- `avg_context_query_chars`：B 组扩展 query 字符数。

### 结果解读

重点看：

1. `context_layer.aggregated` 相比 `baseline.aggregated` 是否提升。
2. `delta.hit_rate` 和 `delta.mrr` 是否为正。
3. `resource_hit_rate` 和 `skill_hit_rate` 是否说明上下文目录定位稳定。

如果 B 组检索指标没有明显提升，但上下文路由指标较好，说明 Context Layer 对“Agent 判断路径”可能有价值，但还需要端到端评测或真实 OpenViking server 接入继续验证。

`--fast` 只用于快速验证评测脚本，会临时关闭 `HYDE_ENABLED` 和 `HYDE_REWRITE_ENABLED`，命令结束后恢复环境变量。正式对比仍建议不加 `--fast`。

## 7. 真实 OpenViking A/B 评测

`openviking_ab` 模式用于验证真实 OpenViking server 的 Resource 检索结果是否能改善本项目的法律检索排序。

### 前置条件

需要先配置并启动 OpenViking server，然后在 `.env` 中配置：

```bash
OPENVIKING_BASE_URL=http://localhost:1933
OPENVIKING_API_KEY=your-openviking-key
```

如果本地 server 不需要 API Key，可以不配置 `OPENVIKING_API_KEY`。

### 语料导入

导入法律法规 Resource：

```bash
python scripts/import_openviking_corpus.py --laws --wait
```

只导入部分领域，适合 smoke test：

```bash
python scripts/import_openviking_corpus.py --laws --domains labor,consumer_protection --wait
```

导入法律流程 Skill：

```bash
python scripts/import_openviking_corpus.py --skills --wait
```

一次性导入法律语料和 Skill：

```bash
python scripts/import_openviking_corpus.py --laws --skills --wait
```

### A/B 分组

- **A 组 baseline**：原始问题直接进入现有 `HybridRetriever.retrieve()`。
- **B 组 openviking**：先调用真实 OpenViking `find()` 在 `viking://resources/laws` 中命中 Resource，再用命中的法律资源 URI 对 HybridRetriever 候选进行 scope/rerank boost。

这里故意不再把 OpenViking 上下文直接拼进 query，因为上一轮 `context_ab` 已经证明这种方式会引入噪声。

### 运行命令

```bash
python eval/run_eval.py --mode openviking_ab --limit 10 --top-k 5
```

输出结果会保存到：

```text
eval/results/eval_openviking_ab_*.json
```

重点观察：

- `baseline.aggregated`
- `openviking.aggregated`
- `delta`
- `openviking_routing.resource_hit_rate`
- `details[].openviking_matches`
