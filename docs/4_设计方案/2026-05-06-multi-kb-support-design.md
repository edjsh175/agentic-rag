# 多知识库支持设计文档

## 背景

当前系统将所有文档存入同一个 Chroma 集合 `rag_knowledge`，检索时无法区分数据来源。用户在 `watch_directory` 下已有按类别组织的子目录结构（如 `用户文档/pdf/`、`技术文档/png/`），但系统未利用这一层级信息。

## 目标

1. 利用 `watch_directory` 的一级子目录作为"知识库"划分
2. 支持前端选择特定知识库进行问答
3. 支持选择"全部知识库"进行混合搜索
4. 最小改动，向后兼容

## 方案：单 Chroma 集合 + Metadata 过滤

保持一个 Chroma 集合，每个文档 chunk 的 metadata 中增加 `kb_name` 字段标记所属知识库。查询时通过 Chroma 的 `filter` 参数按 `kb_name` 过滤。

### 目录到知识库的映射

```
watch_directory/
├── 用户文档/          → kb_name = "用户文档"
│   ├── png/
│   └── pdf/
├── 技术文档/          → kb_name = "技术文档"
│   ├── pdf/
│   └── png/
└── 财务资料/          → kb_name = "财务资料"
    └── ...
```

第一层子目录名 = 知识库名。文件直接在根目录（不在子目录下）属于"未分类"。

### 改动范围

#### 1. Scanner — `rag_knowledge/services/scanner.py`

`_process()` 方法中，从文件相对路径提取一级子目录名：

```python
rel = str(file_path.relative_to(base))
kb_name = rel.parts[0] if len(rel.parts) > 1 else "未分类"
```

每个 chunk 的 metadata 增加字段：

```python
metadata = {
    "source": file_path.name,
    "category": category,
    "kb_name": kb_name,       # ← new
    "kb_path": str(rel.parent),  # ← new
}
```

文件索引 `file_index.json` 每条记录增加 `kb_name` 字段。

#### 2. VectorStore — `rag_knowledge/repository/vector_store.py`

新增带过滤的检索方法（或修改 `search` 对 filter 参数的支持）：

```python
def search(self, query: str, k: int = 4, filter: dict | None = None) -> list[Document]:
    kwargs = {"k": k}
    if filter:
        kwargs["filter"] = filter
    return self._get_store().similarity_search(query, **kwargs)
```

#### 3. RagChain — `rag_knowledge/services/rag.py`

`_retrieve()` 方法增加 `kb_name` 参数，透传给 VectorStore 的 filter：

```python
def _retrieve(self, question: str, kb_name: str | None = None) -> tuple[list[dict], str]:
    search_kwargs = {
        "k": self._retrieval_k,
        "fetch_k": self._retrieval_fetch_k,
        "lambda_mult": self._retrieval_lambda,
    }
    if kb_name:
        search_kwargs["filter"] = {"kb_name": kb_name}
    
    retriever = chroma.as_retriever(
        search_type="mmr",
        search_kwargs=search_kwargs,
    )
    docs = retriever.invoke(question)
```

`query()` 和 `stream_query()` 方法增加 `kb_name` 参数，传入 `_retrieve()`。

系统提示词中 context 格式化增加 kb_name 信息，便于 LLM 回答时引用来源知识库：
```
[来源: report.pdf] [知识库: 用户文档] [类型: pdf]
```

#### 4. API — `rag_knowledge/api/routes.py`

新增接口 `GET /knowledge-bases`，扫描 `watch_directory` 的一级子目录返回列表：

```python
@router.get("/knowledge-bases")
def list_knowledge_bases():
    bases = ["全部知识库"]
    if _cfg and _cfg.watch_dir.exists():
        for d in sorted(_cfg.watch_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                bases.append(d.name)
    return {"bases": bases}
```

`/query` 和 `/query/stream` 内部将 `kb_name` 传给 RagChain。`kb_name=None` 表示全部。

`/upload` 增加 `kb_name` 表单字段，上传后 chunk 的 metadata 中标记 `kb_name`。

#### 5. Models — `rag_knowledge/models/api.py`

`QueryRequest` 中 `collection_name` 字段已存在，保留不动。逻辑上 `kb_name` 覆盖 `collection_name`。

#### 6. API 封装 — `web/src/api/index.ts`

新增方法：

```typescript
export async function getKnowledgeBases(signal?: AbortSignal) {
  return getJSON<{ bases: string[] }>('/knowledge-bases', signal)
}
```

`queryKnowledge` 和 `queryKnowledgeStream` 增加 `kbName` 参数。

#### 7. Vue 组件

**ChatView.vue** — 顶部栏增加知识库选择下拉框：

- 从 `getKnowledgeBases()` 获取列表
- 默认选中"全部知识库"
- 选择存入 localStorage，刷新不丢失
- 问答时传入 `kbName`

**改动量**：约 20 行。

### 向后兼容

- 已有数据没有 `kb_name` 字段 → 查询时 filter 不匹配 → 这些是旧数据
- 旧数据在查询时会是否报错？Chroma 的 `filter` 是无匹配则返回空。但"全部知识库"（不传 filter）会正常查到它们。
- 启动时可以加一个小迁移逻辑：对 `watch_directory` 根目录下的文件给 `kb_name = "未分类"`
- **更简单的做法**：在 ChatView 的下拉选项中，"全部知识库"是默认选项，旧数据不受影响。
- 新增 KB 选择功能后，旧数据不会出现在任何特定 KB 的搜索结果中，它们属于"未分类"，除非重新扫描。

### 时序图

```
用户选择 KB → ChatView (Vue)
                ↓
          GET /knowledge-bases  (启动时)
                ↓
          POST /query { question, kb_name }
                ↓
          Routes → RagChain.query()
                ↓
          VectorStore.search(filter={"kb_name": "..."})
                ↓
          Chroma similarity_search + MMR
                ↓
          返回 docs → LLM 生成回答 → 返回给前端
```

### 不变的部分

- 文件加载（loader）不变
- 向量化（embedding）不变
- 文件索引结构微增字段，加载/保存逻辑不变
- 前端消息渲染、存储逻辑不变
- 博客管理部分不变
