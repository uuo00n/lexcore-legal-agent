# Qwen2.5 法律 HyDE / 问答 LoRA 微调

## 目标

本目录现在包含两条微调链路：

- **HyDE LoRA**：用于你项目里的 RAG 语义检索增强，推荐优先做。
- **法律问答 LoRA**：用于训练一个更懂法律问答风格的生成模型。

`DISC-Law` 数据的真实结构是：

- `DISC-Law-SFT-Pair-QA-released.jsonl` 是 `问题 -> 答案`
- `DISC-Law-SFT-Triplet-QA-released.jsonl` 是 `法条参考 + 问题 -> 答案`

所以：

- 可以训练 HyDE 的“假设性法律文档生成”
- 可以训练法律问答生成
- 不适合直接训练 query rewrite

## 文件

- HyDE 数据构造脚本：[scripts/prepare_hyde_sft_data.py](/Users/didi/Desktop/Legal/scripts/prepare_hyde_sft_data.py)
- HyDE 训练脚本：[scripts/train_qwen_hyde_lora.py](/Users/didi/Desktop/Legal/scripts/train_qwen_hyde_lora.py)
- HyDE 推理脚本：[scripts/infer_qwen_hyde_lora.py](/Users/didi/Desktop/Legal/scripts/infer_qwen_hyde_lora.py)
- LoRA 合并脚本：[scripts/merge_qwen_lora.py](/Users/didi/Desktop/Legal/scripts/merge_qwen_lora.py)
- 训练脚本：[scripts/train_qwen_law_sft_lora.py](/Users/didi/Desktop/Legal/scripts/train_qwen_law_sft_lora.py)
- 推理脚本：[scripts/infer_qwen_law_lora.py](/Users/didi/Desktop/Legal/scripts/infer_qwen_law_lora.py)
- 训练依赖：[requirements-finetune.txt](/Users/didi/Desktop/Legal/requirements-finetune.txt)

## 安装依赖

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
pip install -r requirements-finetune.txt
```

## 1. 生成 HyDE 训练数据

这一步可以在你当前 Mac 本地做，不需要 GPU。

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
python3 scripts/prepare_hyde_sft_data.py
```

输出位置：

```text
/Users/didi/Desktop/Legal/data/finetune/hyde_sft_train.jsonl
```

如果你只想先做小样本试跑：

```bash
python3 scripts/prepare_hyde_sft_data.py --max-samples 2000
```

## 2. 训练 HyDE LoRA

这一步建议在 Linux + NVIDIA GPU 机器跑。

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
python3 scripts/train_qwen_hyde_lora.py
```

如果 GPU 环境支持 `bitsandbytes`，可以先安装：

```bash
pip install bitsandbytes
```

然后启用 4bit：

```bash
python3 scripts/train_qwen_hyde_lora.py --use-4bit
```

训练输出默认保存在：

```text
/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora
```

## 3. 验证 HyDE LoRA

训练完成后运行：

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
python3 scripts/infer_qwen_hyde_lora.py \
  --question "加班不给加班费怎么办？"
```

理想输出不是用户答案，而是一段适合向量检索的法律语义文本，例如包含：

- 加班工资
- 劳动报酬
- 用人单位
- 支付义务
- 劳动合同法 / 劳动法相关语义

## 4. 可选：合并 LoRA

如果你要部署到只接受完整 HuggingFace 模型目录的服务，可以合并：

```bash
python3 scripts/merge_qwen_lora.py \
  --adapter-path /Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora \
  --output-path /Users/didi/Desktop/Legal/models/qwen2_5_hyde_merged
```

项目当前的 `hf_lora` 接入方式不要求合并，直接加载 adapter 即可。

## 5. 接入项目 HyDE

训练完成并且 LoRA 权重放在本地后，在 `.env` 里改：

```env
HYDE_ENABLED=true
HYDE_BACKEND=hf_lora
HYDE_HF_MODEL_PATH=/Users/didi/Desktop/Legal/models/Qwen2.5-7B-Instruct
HYDE_LORA_PATH=/Users/didi/Desktop/Legal/models/qwen2_5_hyde_lora
HYDE_HF_MAX_NEW_TOKENS=220
HYDE_HF_TEMPERATURE=0.2
```

然后重启项目：

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

说明：

- `rewrite_query` 仍然默认走 `HYDE_LLM_BASE_URL` 的轻量模型。
- 如果不想做 query rewrite，只想用 HyDE LoRA，可以设置 `HYDE_REWRITE_ENABLED=false`。

## 6. 法律问答 LoRA

如果你还想训练“法律问答生成模型”，再跑这条线：

```bash
cd /Users/didi/Desktop/Legal
source .venv/bin/activate
python3 scripts/train_qwen_law_sft_lora.py
```

输出位置：

```text
/Users/didi/Desktop/Legal/models/qwen2_5_law_sft_lora
```

验证：

```bash
python3 scripts/infer_qwen_law_lora.py \
  --question "房东不退押金怎么办？"
```

## 当前机器的实际限制

我已经检查过你当前这台机器的 Python 运行时：

- `torch.cuda.is_available() == False`
- `torch.backends.mps.is_available() == False`

这意味着：

- 你当前环境 **没有 CUDA**
- 当前 PyTorch 也 **没有可用 MPS**
- 直接在这台机器上训练 `Qwen2.5-7B`，基本不可行

这意味着：

- 你当前环境没有 CUDA
- 当前 PyTorch 也没有可用 MPS
- 直接在这台机器上训练 `Qwen2.5-7B`，基本不可行

更实际的做法：

- 本机准备数据和代码
- GPU 机器训练
- 把训练好的 `models/qwen2_5_hyde_lora` 拷回项目
- 修改 `.env` 接入

## query rewrite 怎么办

目前不要用 `DISC-Law` 直接训练 query rewrite。

query rewrite 需要的数据格式应该是：

```json
{"question": "老板让我天天加班不给钱怎么办", "query": "劳动合同法 加班工资 劳动报酬 用人单位 未支付加班费"}
```

这类数据可以从你的 `eval/dataset.json` 和本地法库 chunk_id 自动构造一部分，但最好人工抽查后再训练。

当前建议先把 HyDE LoRA 跑通，再做 query rewrite。
