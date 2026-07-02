# 聊天记录服务器端 JSON 存储设计

## 背景

当前聊天记录仅存储在浏览器 localStorage（文本）和 IndexedDB（图片）。存在以下问题：
- 清浏览器数据后对话丢失
- 换设备无法恢复对话
- 无法跨设备共享

目标：将聊天记录存储到服务器，以 JSON 文件形式，使用浏览器指纹作为用户标识（无登录系统）。

## 需求摘要

- 使用浏览器指纹（首次生成+localStorage 固定）作为用户标识
- 文字消息存服务器 JSON，图片 base64 留在浏览器 IndexedDB
- 加载时优先读服务器，无数据则回退到 localStorage
- 每轮 AI 回复完成后自动保存

## 方案选型

选择 **方案 A：单文件存储**，每个指纹对应一个 JSON 文件。

## 后端设计

### 存储路径

```
data/chats/{fingerprint}.json
```

### 文件结构

```json
{
  "fingerprint": "a1b2c3d4e5...",
  "updated_at": "2026-05-07T10:30:00",
  "messages": [
    {
      "id": "1700000000000",
      "role": "user",
      "content": "文字内容",
      "hasImage": true
    },
    {
      "id": "1700000000001",
      "role": "assistant",
      "content": "回答文本",
      "source_documents": [...]
    }
  ]
}
```

说明：
- `hasImage: true` 标记用户消息附带图片，加载时前端从 IndexedDB 回补 imageUrl
- `source_documents` 保留引用来源信息
- `loading` 状态不持久化

### 新增服务模块

`rag_knowledge/services/chat_storage.py`

```python
class ChatStorage:
    def __init__(self, data_dir: Path):
        self._root = data_dir / "chats"
        self._root.mkdir(parents=True, exist_ok=True)

    def load(self, fingerprint: str) -> dict | None
    def save(self, fingerprint: str, messages: list) -> dict
    def delete(self, fingerprint: str) -> None
```

- `load`: 读取 `{fingerprint}.json`，不存在返回 None
- `save`: 全量覆写 `{fingerprint}.json`，含 `updated_at` 时间戳
- `delete`: 删除 `{fingerprint}.json`

### 新增 API 路由

在 `routes.py` 中新增三个接口，通过 `X-Device-Fingerprint` header 传递指纹。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/chat/history` | 加载聊天历史。返回 `200 {messages: [...]}` 或 `404 {detail: "无历史记录"}` |
| `PUT` | `/api/chat/history` | 保存聊天历史。Body: `{messages: [...]}` |
| `DELETE` | `/api/chat/history` | 清空聊天历史 |

## 前端设计

### 指纹生成

`web/src/utils/fingerprint.ts`

首次访问时组合浏览器稳定特征生成简易哈希：
- `navigator.userAgent`
- `navigator.language`
- `screen.width` + `screen.height`
- `Intl.DateTimeFormat().resolvedOptions().timeZone`

存入 `localStorage` 键 `rag-device-fingerprint`，后续直接读取。

### API 调用层

`web/src/api/index.ts` 新增三个方法，通过 axios 的 `headers` 参数携带指纹。

### storage.ts 改造

**加载流程 `loadChatState()`：**

```
1. 读取指纹
2. GET /api/chat/history
   ├─ 200 → 返回 messages（重建 Message 对象，图片标记回补）
   └─ 404 → 读 localStorage → 有数据？返回
                      └─ 没有 → 返回欢迎消息
```

**保存流程 `saveChatState()`：**

```
1. 读取指纹
2. PUT /api/chat/history（messages 中过滤掉 loading 状态）
3. 同步写 localStorage（兼容回退）
```

**清空流程 `clearChatState()`：**

```
1. DELETE /api/chat/history
2. 清 localStorage
3. 清 IndexedDB 图片
```

### 兼容性

- 旧用户：localStorage 有数据但服务器无 → 回退到本地数据，正常展示
- 首次保存时，服务器有数据则覆写，无则新建
- 指纹不变的前提下数据持续可恢复

## 未纳入范围

- 图片上传到服务器
- 多设备同步（指纹机制天然绑定浏览器，同一用户不同设备指纹不同）
- 对话加密
- 自动清理过期对话

## 实施顺序

1. 后端：`ChatStorage` 服务模块
2. 后端：三条 API 路由
3. 前端：`fingerprint.ts` 工具
4. 前端：`api/index.ts` 三个方法
5. 前端：`storage.ts` 改造
6. 验证：启动 → 发消息 → 刷新 → 恢复
