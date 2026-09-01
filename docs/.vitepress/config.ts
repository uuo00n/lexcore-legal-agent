import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid({
  title: '法智',
  description: '中国法律 AI 助手技术文档',
  lang: 'zh-CN',
  base: '/Legal/',
  ignoreDeadLinks: [/localhost/, /^\/Users\/didi\/Desktop\/Legal\/scripts\//],

  themeConfig: {
    nav: [
      { text: '指南', link: '/guide/overview' },
      { text: '项目报告', link: '/report/project-report' },
      { text: '验证报告', link: '/report/test-results' },
      { text: 'API', link: '/api/' },
      { text: '时序图', link: '/sequences/chat-flow' },
    ],

    sidebar: {
      '/report/': [
        {
          text: '项目报告',
          items: [
            { text: '项目介绍报告', link: '/report/project-report' },
            { text: '验证报告', link: '/report/test-results' },
          ]
        }
      ],
      '/guide/': [
        {
          text: '项目指南',
          items: [
            { text: '项目概述', link: '/guide/overview' },
            { text: '系统架构', link: '/guide/architecture' },
            { text: '模块划分', link: '/guide/modules' },
            { text: '开发指南', link: '/guide/development' },
            { text: '部署文档', link: '/guide/deployment' },
          ]
        }
      ],
      '/api/': [
        {
          text: 'API 文档',
          items: [
            { text: '总览', link: '/api/' },
            { text: '对话 (Chat)', link: '/api/chat' },
            { text: '文件上传', link: '/api/upload' },
            { text: '会话管理', link: '/api/threads' },
            { text: '健康检查', link: '/api/health' },
          ]
        }
      ],
      '/sequences/': [
        {
          text: '时序图',
          items: [
            { text: '对话流程', link: '/sequences/chat-flow' },
            { text: 'RAG 检索流程', link: '/sequences/rag-flow' },
            { text: '记忆提取流程', link: '/sequences/memory-flow' },
          ]
        }
      ],
      '/architecture/': [
        {
          text: '架构专题',
          items: [
            { text: '最终架构总览', link: '/architecture/overview' },
            { text: 'Agent 工作流', link: '/architecture/agent-workflow' },
            { text: 'RAG 架构', link: '/architecture/rag' },
            { text: 'Memory 架构', link: '/architecture/memory' },
            { text: 'Tools 架构', link: '/architecture/tools' },
            { text: 'Persistence 架构', link: '/architecture/persistence' },
            { text: 'Observability 架构', link: '/architecture/observability' },
            { text: 'Context Engineering 与 Memory', link: '/architecture/context-engineering-memory' },
            { text: 'PostgreSQL 持久化', link: '/architecture/postgresql-persistence' },
            { text: 'Redis 缓存', link: '/architecture/redis-cache' },
            { text: '法律数据源', link: '/architecture/data-sources' },
          ]
        }
      ],
    },

    outline: { level: [2, 3], label: '目录' },
    search: { provider: 'local' },

    footer: {
      message: '法智 — 中国法律 AI 助手',
    },
  },

  mermaid: {},
})
