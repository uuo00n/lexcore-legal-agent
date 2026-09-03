---
layout: home

hero:
  name: 法智
  text: 中国法律 AI 助手
  tagline: 基于 RAG + ReAct Agent 的智能法律问答系统
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/overview
    - theme: alt
      text: API 文档
      link: /api/

features:
  - title: 混合检索 RAG
    details: 语义检索 + BM25 关键词 + RRF 融合 + Reranker 精排，覆盖 70 部中国法律法规
  - title: Supervisor 多智能体
    details: Intent Router、Planner、三个 Specialist 与 Result Verifier 组成 19 节点 LangGraph；FastMCP 另行独立暴露 10 个法律工具
  - title: 五层记忆架构
    details: Working Memory、对话窗口、增量摘要、长期语义记忆与 PostgreSQL 持久化工作流状态
  - title: 流式对话
    details: SSE 实时推送节点进展、工具调用、上下文压缩状态与回答内容
---
