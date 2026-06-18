# 记忆提取流程

## 时序图

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant BG as BackgroundTask
    participant EXT as memory_extractor
    participant LLM as 主 LLM
    participant SQLITE as SQLite
    participant CHROMA as ChromaDB

    Note over API: 对话 SSE 流结束后
    API->>BG: add_task(extract_memory)
    Note over API: 立即返回，不阻塞用户

    BG->>EXT: extract_and_save_memory(thread_id, messages)

    Note over EXT,LLM: 1. 增量摘要
    EXT->>SQLITE: 获取现有摘要
    SQLITE-->>EXT: 旧摘要（可能为空）
    EXT->>LLM: 摘要 prompt（旧摘要 + 新消息）
    LLM-->>EXT: 更新后的摘要
    EXT->>SQLITE: 保存新摘要

    Note over EXT,LLM: 2. 长期记忆提取
    EXT->>LLM: 记忆提取 prompt（从对话中提取关键事实）
    LLM-->>EXT: 提取的记忆条目列表
    EXT->>CHROMA: 存入 memory collection（带时间戳）

    Note over EXT,LLM: 3. 用户画像更新
    EXT->>LLM: 画像提取 prompt（身份、关注领域）
    LLM-->>EXT: 画像更新字段
    EXT->>SQLITE: 更新 user_profiles 表
```

## 4 层记忆架构

```mermaid
graph TB
    subgraph 短期["短期记忆"]
        SW[滑动窗口<br/>最近 8 条消息]
    end

    subgraph 中期["中期记忆"]
        SUM[增量摘要<br/>历史对话压缩]
    end

    subgraph 长期["长期记忆"]
        LTM[语义记忆<br/>ChromaDB 向量检索]
    end

    subgraph 画像["用户画像"]
        PROF[实体记忆<br/>身份 / 关注领域]
    end

    SW -->|溢出| SUM
    SUM -->|关键事实| LTM
    SW -->|实体提取| PROF

    style SW fill:#e1f5fe
    style SUM fill:#fff3e0
    style LTM fill:#e8f5e9
    style PROF fill:#fce4ec
```

## 各层详解

### 滑动窗口（短期）

- 容量：最近 8 条消息（`SLIDING_WINDOW_SIZE`）
- 直接发送给 LLM，无需额外处理
- 超出窗口的消息触发摘要生成

### 增量摘要（中期）

- 存储：SQLite `summaries` 表
- 每次对话结束后，LLM 将旧摘要 + 新消息压缩为更新的摘要
- 注入方式：作为系统提示词的一部分发送给 LLM

### 长期语义记忆

- 存储：ChromaDB `memory` collection
- 内容：从对话中提取的关键事实、用户偏好、重要结论
- 检索：按用户最新问题做语义相似度检索，取 top 3
- 衰减：检索时考虑时间新鲜度权重

### 用户画像（实体记忆）

- 存储：SQLite `user_profiles` 表
- 字段：`identity`（身份）、`focus_areas`（关注领域列表）
- 更新：每次对话后增量更新，不覆盖已有信息
- 注入方式：作为系统提示词的用户背景部分
