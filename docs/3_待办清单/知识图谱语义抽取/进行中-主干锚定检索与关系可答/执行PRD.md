# 执行 PRD — 主干产品线实体锚定检索与关系可答

- **记录日期**：2026-07-22
- **状态**：**进行中**（P0 已落地；图谱 chunk 召回已关闭延后）
- **目录**：`docs/3_待办清单/知识图谱语义抽取/进行中-主干锚定检索与关系可答/`
- **母文档**：[`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`](../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md)
- **关联总览**：[`../2026-07-13-知识图谱PRD剩余轮次总览.md`](../2026-07-13-知识图谱PRD剩余轮次总览.md)
- **前置**：第 2.5 轮产品关系主干已正式入库；第 3 轮规则 rebuild-safe 已完成（实 LLM 补抽另计）
- **配置口径（2026-07-22）**：
  - `[graph_retrieval] enabled = false`（**图谱 chunk 召回/融合关闭，延后**）
  - `[graph_retrieval] query_rewrite_enabled = true`（**主干锚定改写仍开启**）
  - Ollama：`http://192.168.10.158:11434`（须绕过本机系统代理；`Config` 已自动写入 `NO_PROXY`）

---

## 1. 本阶段要解决的问题

```text
❌ 用户口语/别名无法稳定落到图谱专业术语，Hybrid 容易找错产品 chunk
❌ 图改写曾依赖「先 EntityLinker 精确命中」——口语对不上就整条跳过
❌ 回答侧不清楚当前锚点与主干关系，产品关系题易串实体或无法表述归属
❌ 图谱 chunk 召回与主线「术语锚定」耦合过紧，验收口径不清
```

**本阶段目标（一句话）**：  
在**只使用产品关系主干**的前提下，把用户非专业说法改写成主干 canonical 检索 query，驱动 Hybrid 找对位置；并把锚点 + 主干一跳关系交给回答 LLM，使其能介绍产品并答产品关系。  
**图谱侧 chunk 召回本阶段不做**，延后单独验收。

「定位优先」含义（纠正后口径）：

1. **检索不要找错位置**（问 StampManager → 围绕 StampManager 的介绍/相关 chunk，而不是 Tools/步骤词带走）。
2. **架构产物要交给回答 LLM**（知道锚点是谁、主干上和谁什么关系）。

---

## 2. 目标链路（当前）

```text
用户问题
  → 主干约束包（product_relation_backbone.json）
  → 软命中 + 改写 LLM（解构 + canonical + avoid + anchored_queries）
  → Hybrid 多路检索（含 graph_rewrite 高权重 query）
  → 回答 LLM（context + 锚点/关系摘要注入）
```

**明确关闭**：`GraphRetriever` 从图库取 `entity_chunk_links` 并入结果（`enabled=false`）。

分工：

| 步骤 | 执行者 | 产出 |
|------|--------|------|
| 1 解构请求 | 改写 LLM（一次 JSON） | intent / surface_terms |
| 2a 术语锚定 | 改写 LLM + soft_match | canonical / avoid / anchored_queries |
| 2b 知识检索 | Hybrid（非图 chunk） | context + 引用 |
| 3 成文 | 回答 LLM | 介绍/关系回答（事实以 context 为准） |

---

## 3. 已完成工作（截至 2026-07-22）

### 3.1 主干与重建底座（本阶段前置，已完成）

- 预览确认 → 正式 [`data/product_relation_backbone.json`](../../../../data/product_relation_backbone.json)（约 147 实体 / 175 关系）
- 废弃 seed 清理 + 主干 sync 分拆审批 apply；Task 8.1 Gate PASS
- 第 3 轮 `rebuild-safe` **规则路径** execute / post-audit / 归档（实 LLM 曾因 Ollama 不可达为 stub，**不算 LLM 验收通过**）

### 3.2 Backbone Guard 与抽取侧（已完成）

- [`rag_knowledge/services/backbone_guard.py`](../../../../rag_knowledge/services/backbone_guard.py)：冲突校验、邻域判定、`format_backbone_context`
- 抽取 staging 冲突即 rejected；LLM prompt 注入主干上下文
- Ollama fail-fast（`ollama_health`）；Round3 脚本禁止空 stub；邻域优先 LLM 抽取

### 3.3 主干锚定改写 + 关系可答（已完成，P0）

| 能力 | 说明 | 关键代码 |
|------|------|----------|
| 软命中 | 归一化空格/大小写，匹配主干 name/alias | `soft_match_backbone_entities` |
| 改写 JSON | 解构 / canonical / avoid / anchored_queries / relation_focus | `GraphQueryRewriter.anchor_from_backbone` |
| soft_hit 强制优先 | 有 soft_hit 时 LLM 不得改锚到其它实体（如 PipelineBuilder→PipelineWebGL） | `_finalize_anchor_payload` |
| 对比题 query | alias 等价保护（StampTools↔StampGIS Tools），避免 protect 清空 | `query_entity_guard` + 启发式回补 |
| 检索接线 | 检索前合并高权重 `graph_rewrite` query；无图 retriever 也可改写 | `RagChain._apply_backbone_anchor_rewrite` |
| 回答注入 | 锚点 + 主干一跳关系摘要；规则：介绍紧扣锚点、关系可用边骨架 | `_SYSTEM_PROMPT` / `_build_messages` |
| 代理坑 | 访问 `192.168.10.158` 时系统代理易 502；Config 自动补 `NO_PROXY` | `Config._ensure_ollama_bypasses_system_proxy` |

### 3.4 配置与验收快照

- 图谱 chunk：`[graph_retrieval] enabled = false`（2026-07-22 按决策关闭）
- 锚定改写：`query_rewrite_enabled = true`
- 单测：`test_graph_query_rewrite` / `test_query_entity_guard` / `test_backbone_guard` 相关用例已通过
- 实 LLM 冒烟（helper=`gemma3:4b`）：StampManager / Tools↔Server / PipelineBuilder 锚定与 query 已复测通过

---

## 4. 后续计划

### 4.1 本阶段剩余（P1，问答主线）

| ID | 项 | 说明 | 优先级 |
|----|----|------|--------|
| A1 | 端到端问答页验收 | 重启后端后固定题集：StampManager 介绍、Tools vs Server、PipelineBuilder 归属、口语管线工具；核对来源是否跟锚点一致 | P1 |
| A2 | 口语别名补强 | 如「管线工具」→ PipelineBuilder 写入主干 aliases（无 soft_hit 时小模型易猜错 PipelineWebGL） | P1 |
| A3 | helper 模型评估 | `gemma3:4b` 词表大时偶发慢/偏；可评估更稳小模型或缩短 lexicon | P1 |
| A4 | 可观测性 | 可选：日志/debug 露出 canonical 与 anchored_queries（不改 API 契约亦可） | P2 |

### 4.2 明确延后（不挡本阶段）

| ID | 项 | 说明 |
|----|----|------|
| D1 | **图谱 chunk 召回/融合** | 重新打开 `graph_retrieval.enabled`，并单独验收「图 chunk 是否帮到定位」 |
| D2 | 第 3 轮实 LLM 补抽 | Ollama 已通可择机重跑；与问答锚定并行，**不互相替代** |
| D3 | 第 4 轮 GraphRAG A/B | 建议本阶段 A1 过后再开，避免测到旧图召回行为 |
| D4 | 全图模糊链接 / UI 展示「口语→术语」 | 非本阶段必需 |

### 4.3 建议实施顺序

```text
1. 重启后端，确认 enabled=false 且 rewrite 仍生效
2. A1 问答页固定题集人工验收
3. 按失败题补 A2 别名或调 A3 helper
4. 本阶段收口纪要 → 再评估是否打开 D1 图 chunk
5. D2 / D3 按总览轮次推进
```

---

## 5. 成功标准（本阶段）

- [ ] 口语/别名题：改写 `canonical` 与 `anchored_queries` 落在主干正确实体上（soft_hit 场景不得被 LLM 改锚）
- [ ] Hybrid 检索结果主体与锚点一致（抽检 StampManager / StampGIS Tools / PipelineBuilder）
- [ ] 产品关系题：回答侧 prompt 含主干一跳摘要；有 context 时可正确表述归属/区分
- [x] **图谱 chunk 召回关闭**，不作为本阶段通过条件
- [ ] 改写失败时降级不阻断主问答

---

## 6. 关键文件与配置

| 路径 | 角色 |
|------|------|
| `data/product_relation_backbone.json` | 主干真源 |
| `rag_knowledge/services/backbone_guard.py` | 软命中 / 关系摘要 / Guard |
| `rag_knowledge/services/graph_query_rewrite.py` | 锚定改写 |
| `rag_knowledge/services/query_entity_guard.py` | alias 等价保护 |
| `rag_knowledge/services/rag.py` | 接线与回答注入 |
| `rag_knowledge/config.py` | `NO_PROXY` 旁路 |
| `config.ini` `[graph_retrieval]` | `enabled=false`；`query_rewrite_enabled=true` |
| `tests/test_graph_query_rewrite.py` 等 | 回归 |

---

## 7. 风险与注意事项

1. **`127.0.0.1:11434` ≠ 远程 Ollama**：远程机须用局域网 IP（当前 `192.168.10.158`）。
2. **Windows 系统代理**可能导致对局域网 Ollama 返回空 502；依赖 `NO_PROXY` 或系统旁路。
3. 关闭图 chunk 后，关系题更依赖「摘要骨架 + 文本 chunk」；若介绍类文档未入库，仍会「锚对了但 context 空」。
4. 第 3 轮实 LLM / 第 4 轮 A/B 的文档状态勿与本阶段「锚定问答」混淆——分线推进。

---

## 8. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-22 | 初版：汇总已完成锚定主线、P0、配置关闭图 chunk、后续 A/D 计划 |
