# 智能体预设系统设计

## 背景

当前系统使用固定 system prompt 和用户手动选择模型/知识库/工具开关。用户需要预设的"智能体"角色，一键切换整套配置（角色描述 + 模型 + 知识库 + 工具开关）。

## 需求摘要

- 定义 3-5 个预设智能体，有名称、图标、角色描述
- 每个智能体包含：system prompt、模型、知识库范围、联网搜索/深度思考开关
- 用户点选智能体后，自动应用其全套配置
- 智能体配置存储在服务器端 JSON 文件

## 数据模型

### agents.json 文件结构

位于 `data/agents.json`：

```json
[
  {
    "id": "general",
    "name": "通用助手",
    "icon": "🤖",
    "description": "默认助手，回答各类问题",
    "system_prompt": "你是一位通用助手，基于知识库和专业能力回答用户问题。",
    "model": null,
    "kb_name": null,
    "web_search": false,
    "thinking": true
  },
  {
    "id": "code-reviewer",
    "name": "代码审查",
    "icon": "🛡️",
    "description": "严格审查代码安全性、性能和可维护性",
    "system_prompt": "你是资深代码审查专家。审查以下代码时关注：1) 安全漏洞 2) 性能问题 3) 代码规范 4) 可维护性。给出具体改进建议。",
    "model": "deepseek-r1:7b",
    "kb_name": null,
    "web_search": false,
    "thinking": true
  },
  {
    "id": "writer",
    "name": "文案助手",
    "icon": "✍️",
    "description": "擅长写文章、报告、方案等各类文稿",
    "system_prompt": "你是专业文案写手。根据用户需求撰写清晰、有逻辑、语言优美的文稿。注意格式规范，适当使用 Markdown。",
    "model": null,
    "kb_name": "文章附件",
    "web_search": true,
    "thinking": false
  }
]
```

字段说明：
- `model: null` 表示使用系统默认模型
- `kb_name: null` 表示不限制知识库范围
- 未指定的开关（thinking, web_search）默认关闭

## 后端设计

### 新增 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/agents` | 返回智能体列表 |

### 新增 service

`rag_knowledge/services/agent_service.py` 或直接在 `routes.py` 中读取 JSON

```python
def load_agents() -> list[dict]:
    path = Config().data_dir / "agents.json"
    if not path.exists():
        return [default_agent()]
    return json.loads(path.read_text(encoding="utf-8"))
```

### system prompt 覆盖

`rag.py` 中 `_build_messages` 和 `_SYSTEM_PROMPT` 的修改：

- `_build_messages` 新增可选参数 `agent_prompt: str | None = None`
- 当 `agent_prompt` 提供时，替换 `_SYSTEM_PROMPT` 内容（保留 `{context}` 占位符替换逻辑）
- `query` / `stream_query` 新增 `agent_prompt` 参数，透传给 `_build_messages`

### Route 修改

`query` / `query/stream` 路由新增 `agent_prompt` 请求字段，透传给 `_rag.query()` / `_rag.stream_query()`

## 前端设计

### 获取智能体列表

页面加载时调用 `GET /api/agents`，结果缓存到前端一个 `agents` ref 中。

### 智能体选择器

在聊天界面顶部导航栏区域（或左侧），新增智能体切换 UI：

- 点击智能体名称 → 弹出下拉列表或面板
- 面板中列出所有智能体，显示 name + icon + description
- 选中后：
  - 更新当前 system prompt（发送请求时携带）
  - 切换模型（如果 agent 指定了）
  - 切换知识库（如果 agent 指定了）
  - 切换 thinking / web_search 开关

### 选中状态

- 当前选中的智能体 ID 存入 `localStorage: rag-active-agent`
- 页面刷新后恢复

### 智能体数据流

```
页面加载 → GET /api/agents → 缓存 agents[]
                    ↓
从 localStorage 恢复 agentId → 找到对应 agent → 应用配置
                    ↓
用户提问 → POST /query (携带 agent_prompt 和其他配置)
                    ↓
后端 → _build_messages(agent_prompt) → 用 agent prompt 替代默认 system prompt
```

## 智能体切换与当前设置的关系

- 切换智能体时，覆盖对应的配置项
- 用户仍可以手动调整模型/开关，但切换智能体会重置
- 当前 active 的智能体名称显示在选择器上

## 默认智能体

首次部署无 `agents.json` 时，系统自动创建一个"通用助手"智能体作为默认值。

## 未纳入范围

- 可视化编辑智能体（直接编辑 JSON 文件）
- 用户自定义创建智能体
- 智能体共享/导入导出
- 每个智能体独立对话历史
