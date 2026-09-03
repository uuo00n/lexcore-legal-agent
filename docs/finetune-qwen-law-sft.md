# Qwen2.5 法律 HyDE / 问答 LoRA 微调

## 目标

仓库里有两条互相独立的微调链路：

- **HyDE LoRA**：给 RAG 语义检索生成"假设性法律文档"，对检索指标的影响最直接，建议优先做。
- **法律问答 LoRA**：训练一个更贴近法律问答风格的生成模型，与检索链路无关。

`DISC-Law` 数据的真实结构是：

- `DISC-Law-SFT-Pair-QA-released.jsonl` 是 `问题 -> 答案`
- `DISC-Law-SFT-Triplet-QA-released.jsonl` 是 `法条参考 + 问题 -> 答案`

所以：

- 可以训练 HyDE 的"假设性法律文档生成"
- 可以训练法律问答生成
- 不适合直接训练 query rewrite

## 脚本清单

| 用途 | 脚本 |
|------|------|
| HyDE 数据构造 | `scripts/prepare_hyde_sft_data.py` |
| HyDE 训练 | `scripts/train_qwen_hyde_lora.py` |
| HyDE 推理验证 | `scripts/infer_qwen_hyde_lora.py` |
| LoRA 合并（可选） | `scripts/merge_qwen_lora.py` |
| 法律问答训练 | `scripts/train_qwen_law_sft_lora.py` |
| 法律问答推理验证 | `scripts/infer_qwen_law_lora.py` |
| 训练额外依赖 | `requirements-finetune.txt` |

::: warning 六个脚本的默认路径仍写死为 macOS 绝对路径
`DEFAULT_MODEL_PATH` / `DEFAULT_DATASET_PATH` / `DEFAULT_OUTPUT_DIR` 这类常量目前的取值形如
`/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct`，见
`scripts/prepare_hyde_sft_data.py:17-19`、`scripts/train_qwen_hyde_lora.py:23-25`、
`scripts/train_qwen_law_sft_lora.py:30-33`、`scripts/merge_qwen_lora.py:12-14`，以及两个
`infer_*` 脚本的第 12-13 行。这些默认值只在原作者那台 Mac 上成立，其他机器必须显式传
`--model-path` / `--dataset-path` / `--output-dir` / `--adapter-path`，否则脚本会在一个
不存在的目录上失败。下面所有命令示例都补齐了这些参数。
:::

## 前置准备

### 依赖

`requirements-finetune.txt` 只是 `requirements.txt` 的增量（`datasets`、`peft`、`accelerate`、
`trl`），`transformers` 与 PyTorch 由主依赖提供。它只在训练机上需要，不要装进部署环境。

```bash
# 仓库根目录
source .venv/bin/activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-finetune.txt
```

### 数据与基座模型

两者都不在 Git 里，需要自己准备：

| 内容 | 期望位置 | 说明 |
|------|----------|------|
| DISC-Law SFT 语料 | `data/DISC-Law-SFT-Pair-QA-released.jsonl`、`data/DISC-Law-SFT-Triplet-QA-released.jsonl` | 从 DISC-Law 官方发布页下载 |
| Qwen2.5 基座权重 | `models/Qwen2.5-7B-Instruct` | 仓库 `models/` 目录当前只有 `bge-small-zh-v1.5` 与 `bge-reranker-base` |

放到别处也可以，只要命令里的 `--pair-path` / `--triplet-path` / `--model-path` 跟着改。

## 1. 生成 HyDE 训练数据

纯数据处理，不需要 GPU，在开发机上跑即可。

```bash
python scripts/prepare_hyde_sft_data.py \
  --pair-path data/DISC-Law-SFT-Pair-QA-released.jsonl \
  --triplet-path data/DISC-Law-SFT-Triplet-QA-released.jsonl \
  --output-path data/finetune/hyde_sft_train.jsonl
```

先做小样本试跑时加 `--max-samples 2000`（默认 `0` 表示不限制）。其他可调项：
`--max-answer-chars`（默认 700，截断过长答案）、`--seed`（默认 42）。

## 2. 训练 HyDE LoRA

建议在 Linux + NVIDIA GPU 机器上跑。

```bash
python scripts/train_qwen_hyde_lora.py \
  --model-path models/Qwen2.5-7B-Instruct \
  --dataset-path data/finetune/hyde_sft_train.jsonl \
  --output-dir models/qwen2_5_hyde_lora
```

默认超参：`--max-length 1536`、`--num-train-epochs 1`、`--learning-rate 2e-4`、
`--per-device-train-batch-size 1`、`--gradient-accumulation-steps 8`、
LoRA `r=16 / alpha=32 / dropout=0.05`。

显存不够时可以走 4bit QLoRA，先装 `bitsandbytes` 再加开关：

```bash
pip install bitsandbytes
python scripts/train_qwen_hyde_lora.py --use-4bit \
  --model-path models/Qwen2.5-7B-Instruct \
  --dataset-path data/finetune/hyde_sft_train.jsonl \
  --output-dir models/qwen2_5_hyde_lora
```

## 3. 验证 HyDE LoRA

```bash
python scripts/infer_qwen_hyde_lora.py \
  --model-path models/Qwen2.5-7B-Instruct \
  --adapter-path models/qwen2_5_hyde_lora \
  --question "加班不给加班费怎么办？"
```

理想输出不是给用户看的答案，而是一段适合向量检索的法律语义文本，例如包含：

- 加班工资
- 劳动报酬
- 用人单位
- 支付义务
- 劳动合同法 / 劳动法相关语义

## 4. 可选：合并 LoRA

只有部署到「只接受完整 HuggingFace 模型目录」的服务时才需要合并：

```bash
python scripts/merge_qwen_lora.py \
  --model-path models/Qwen2.5-7B-Instruct \
  --adapter-path models/qwen2_5_hyde_lora \
  --output-path models/qwen2_5_hyde_merged
```

项目自带的 `hf_lora` 接入方式直接加载 adapter，不要求合并。

## 5. 接入项目 HyDE

权重放好后，在 `.env` 里切换后端（`.env.example` 已带同名条目，改值即可）：

```ini
HYDE_ENABLED=true
HYDE_BACKEND=hf_lora
HYDE_HF_MODEL_PATH=models/Qwen2.5-7B-Instruct
HYDE_LORA_PATH=models/qwen2_5_hyde_lora
HYDE_HF_MAX_NEW_TOKENS=220
HYDE_HF_TEMPERATURE=0.2
```

`HYDE_BACKEND` 默认是 `openai`（走 `HYDE_MODEL=deepseek-v4-flash`），只有显式改成 `hf_lora`
才会加载本地权重。两个路径环境变量必须设置：`services/retriever/hyde.py:143-150` 的内置兜底
值同样是那台 Mac 的绝对路径，缺省时会去找一个不存在的目录。

然后重启服务：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 \
  --loop services.checkpoint:selector_event_loop_factory
```

说明：

- query rewrite 与 HyDE 是两件事，`rewrite_query` 仍然走 `HYDE_LLM_BASE_URL` 的轻量模型。
- 只想用 HyDE LoRA、不想做 query rewrite，设 `HYDE_REWRITE_ENABLED=false`。
- 本地 7B 推理会显著拉长首字延迟，`HYDE_BACKEND=hf_lora` 只适合离线评测与实验，线上默认仍用云端轻量模型。

## 6. 法律问答 LoRA

这条线与检索无关，训练出来的是一个独立的问答模型，不会自动接进 Agent 主链。

```bash
python scripts/train_qwen_law_sft_lora.py \
  --model-path models/Qwen2.5-7B-Instruct \
  --pair-path data/DISC-Law-SFT-Pair-QA-released.jsonl \
  --triplet-path data/DISC-Law-SFT-Triplet-QA-released.jsonl \
  --output-dir models/qwen2_5_law_sft_lora
```

默认 `--max-length 2048`、`--num-train-epochs 2`，比 HyDE 那条线更重。验证：

```bash
python scripts/infer_qwen_law_lora.py \
  --model-path models/Qwen2.5-7B-Instruct \
  --adapter-path models/qwen2_5_law_sft_lora \
  --question "房东不退押金怎么办？"
```

## 硬件限制

先自查一遍训练机，两个都是 `False` 就不用往下试了：

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.backends.mps.is_available())"
```

项目的运行时依赖（`requirements.txt`、Docker 镜像）装的是 CPU 版 PyTorch，embedding 与
reranker 都跑在 CPU 上，本身不需要 GPU。但 `Qwen2.5-7B` 的 LoRA 微调是另一回事：纯 CPU 环境
基本不可行，Apple MPS 也只能跑通流程、算不完一个 epoch。

可行的分工：

- 开发机准备数据和代码（第 1 步）
- GPU 机器训练（第 2 / 6 步）
- 把 `models/qwen2_5_hyde_lora` 拷回项目
- 改 `.env` 接入（第 5 步）

## query rewrite 怎么办

目前不要用 `DISC-Law` 直接训练 query rewrite —— 它没有「原始提问 → 检索查询」这一对监督信号。

query rewrite 需要的数据格式应该是：

```json
{"question": "老板让我天天加班不给钱怎么办", "query": "劳动合同法 加班工资 劳动报酬 用人单位 未支付加班费"}
```

这类数据可以从 `eval/dataset.json` 和本地法库的 chunk_id 自动构造一部分，但最好人工抽查后再训练。

当前建议先把 HyDE LoRA 跑通，再做 query rewrite。
