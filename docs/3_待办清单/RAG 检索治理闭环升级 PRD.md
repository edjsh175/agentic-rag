# RAG 检索治理闭环升级 PRD

## 1. 项目背景

当前 RAG 系统已经完成：

* DOCX 结构化解析
* Chroma 向量检索
* BM25 混合检索
* reranker 重排序
* Graph-RAG 实体约束
* Retrieval Intent Profile 治理

近期进行了知识库受控重建。

重建解决了：

* file_index 与 Chroma 不一致
* chunk 丢失
* 孤儿 chunk
* 图谱失同步

但同时暴露新的工程问题：

固定评测集仍然绑定旧 chunk_id，导致 A/B 回归测试出现假失败。

---

# 2. 当前问题定义

## 2.1 核心问题

评测数据：

```json
{
 "question": "...",
 "relevant_chunk_ids": [
   "old_chunk_uuid"
 ]
}
```

知识库：

```
rebuild

old_chunk_uuid
        ↓
new_chunk_uuid
```

导致：

```
retrieved_chunk_ids ∩ relevant_chunk_ids = empty
```

最终：

```
Recall = 0
MRR = 0
Hit Rate = 0
```

但实际检索能力可能没有下降。

---

# 3. 当前已完成能力

## 3.1 Retrieval Intent Profile

已完成：

### 配置契约

要求：

* id 必须存在
* entity_aliases 或 intent_terms 必须存在
* preferred_sources/fallback_sources 不允许冲突
* candidate_min_k 必须合法

### 防误伤机制

已实现：

* source-only 不生效
* 必须命中实体/意图/章节/召回锚点
* sibling penalty 不压制目标族
* 普通查询不会被 profile 接管

### 测试覆盖

已覆盖：

* 非法 profile
* source-only 偏置
* 中性查询
* 误伤场景

当前：

```
21 passed
```

---

# 4. 当前缺陷

## 4.1 Hardcases 评测集生命周期管理缺失

当前问题：

知识库重建后：

```
chunk_id变化
```

但是：

```
eval_dataset_hardcases.json
```

没有同步。

## 影响

任何以下操作都会导致：

* 重建知识库
* 修改 chunk size
* 修改 splitter
* 修改 embedding
* 重新导入文档

固定评测失效。

---

# 5. 产品目标

建立完整 RAG Evaluation Governance。

目标：

任何知识库变化后：

1. 自动发现评测资产失效
2. 阻止错误回归判断
3. 支持重新校准
4. 保留历史指标连续性

---

# 6. 功能需求

## 6.1 Dataset Health Check

新增：

```
check_eval_dataset_health()
```

输入：

```
eval_dataset_hardcases.json
```

检查：

### chunk 存活率

例如：

```
total relevant ids:
18

existing:
18

missing:
0

health:
100%
```

异常：

```
existing:
0

health:
0%

拒绝运行
```

---

## 6.2 评测集格式升级

当前：

```json
{
 "question":"",
 "relevant_chunk_ids":[]
}
```

升级：

```json
{
 "question":"",

 "expected_targets":[
   {
     "source":"",
     "section_path":"",
     "keywords":[]
   }
 ],

 "chunk_ids":[]
}
```

其中：

chunk_id:

用途：

* 快速匹配
* 缓存

不是唯一依据。

---

# 7. A/B 测试流程升级

## 当前流程

```
run_retrieval_ab

 ↓

执行检索

 ↓

计算指标

 ↓

发现归零

 ↓

提示 stale
```

## 新流程

```
run_retrieval_ab

 ↓

dataset health check

 ↓

通过

 ↓

执行A/B


失败:

dataset invalid

停止
```

---

# 8. 必跑验收流程

## 8.1 Profile 修改

必须：

```bash
pytest tests/test_retrieval_intent.py \
tests/test_routing_and_structured_boost.py \
tests/test_retrieval_regression.py
```

---

## 8.2 Integration

必须：

```bash
pytest tests/test_retrieval_regression.py -m integration
```

---

## 8.3 数据健康检查

必须：

```bash
python check_eval_dataset.py
```

输出：

```
dataset:
eval_dataset_hardcases.json

chunk health:
100%

status:
PASS
```

---

## 8.4 A/B

最后：

```bash
python run_retrieval_ab.py \
data/eval_dataset_hardcases.json \
--fail-on-regression
```

---

# 9. 数据重建流程

当发生：

* 知识库重建
* chunk变化
* 文档重新解析

执行：

## Step 1

冻结旧指标：

```
retrieval_ab_results_archive.json
```

## Step 2

重新校准 hardcases

重新确认：

* source
* section
* content

## Step 3

生成新的：

```
eval_dataset_hardcases.json
```

## Step 4

重新跑：

```
A/B baseline
```

---

# 10. 不允许的行为

禁止：

## 直接提高 profile 权重

例如：

```
preferred_source +1
```

解决不了真实召回问题。

---

禁止：

## 用 source 覆盖语义问题

错误：

```
所有 StampTools 文档提升
```

正确：

```
实体 + 意图 + 章节
同时满足
```

---

禁止：

## 用关闭 fail-on-regression 掩盖问题

如果：

```
指标归零
```

必须先检查：

```
dataset health
```

---

# 11. 当前优先级

## P0

完成：

* hardcases 健康检查
* chunk_id 脱敏升级
* 重新生成评测基准

## P1

完善：

* content fingerprint
* section_path 校验
* source 校验

## P2

增强：

* 自动生成 hardcases
* 人工审核工作流
* 指标历史版本管理

---

# 12. 最终验收标准

达到：

```
知识库重建

↓

评测集自动检测

↓

不会出现假回归

↓

A/B结果可信

↓

profile调整可量化
```

最终形成：

```
数据层
  ↓
检索层
  ↓
治理层
  ↓
评测层
  ↓
发布门禁
```

完整 RAG 工程闭环。


# RAG 全链路治理审查与整改 PRD

## 1. 项目现状

当前系统已经具备：

* DOCX 结构化解析
* Chunk 分割
* Chroma 向量库
* BM25 混合检索
* Reranker
* Graph-RAG
* Entity Guard
* Retrieval Intent Profile
* A/B 检索评估
* 前后端测试体系

本轮全面审查目标：

确认：

1. 当前代码是否存在链路断点
2. 测试是否可信
3. 评估体系是否可信
4. 数据重建是否会破坏治理流程

---

# 2. 全链路检查结果

## 2.1 后端测试

执行：

```bash
python -m pytest -q
```

结果：

```
371 passed
6 deselected
10 subtests passed
```

结论：

后端测试链路正常。

---

## 2.2 Integration 测试

执行：

```bash
pytest -m integration -q
```

结果：

```
6 passed
```

覆盖：

* 真实知识库检索
* Entity Guard
* RAG pipeline

结论：

真实运行链路正常。

---

## 2.3 依赖检查

执行：

```bash
python -m pip check
```

结果：

```
No broken requirements found
```

结论：

Python 环境正常。

---

## 2.4 编译检查

执行：

```bash
python -m compileall
```

结果：

通过。

结论：

不存在语法级断裂。

---

## 2.5 前端检查

执行：

```bash
npm run check
```

结果：

测试：

```
20 passed
```

构建：

```
vite build success
```

结论：

前端链路正常。

---

# 3. 当前真正问题

## 3.1 Hardcases 评测集失效

现象：

运行：

```bash
run_retrieval_ab.py
```

出现：

```
Regression detected

recall@3 dropped
mrr dropped
overall_hit_rate dropped
```

进一步检查：

eval_dataset_hardcases.json:

```
144 questions

18 unique relevant_chunk_ids
```

当前 Chroma：

```
existing:
0

missing:
18
```

说明：

评测集绑定旧知识库。

---

# 4. 根因分析

## 原流程

```
DOCX

↓

chunk生成

↓

chunk_id

↓

评测集保存chunk_id

↓

A/B比较
```

问题：

知识库重建：

```
old chunk_id

↓

new chunk_id
```

但是：

```
eval_dataset_hardcases.json
```

没有同步。

导致：

```
检索正确

但是评测认为错误
```

---

# 5. 必须整改

## P0：增加 Evaluation Dataset Health Check

新增：

```
check_eval_dataset_health()
```

运行 A/B 前：

检查：

* chunk_id 存活率
* source 是否存在
* section_path 是否存在

例如：

正常：

```
dataset:
hardcases

chunk health:
100%

PASS
```

异常：

```
dataset:
hardcases

chunk health:
0%

BLOCK
```

禁止继续跑指标。

---

# 6. P0：升级评测数据格式

当前：

```json
{
 "question":"",
 "relevant_chunk_ids":[]
}
```

升级：

```json
{
 "question":"",

 "expected_targets":[
   {
    "source":"",
    "section_path":"",
    "keywords":[]
   }
 ],

 "chunk_ids":[]
}
```

规则：

chunk_id：

* 用于加速
* 不是唯一依据

真实判断：

```
source
+
section_path
+
content similarity
```

---

# 7. P1：建立知识库变更后的评测流程

任何：

* 重建知识库
* 修改 splitter
* 修改 embedding
* 修改 chunk size
* 修改文档解析

必须：

## Step 1

冻结旧结果：

```
retrieval_ab_results_archive.json
```

## Step 2

重新校准 hardcases

## Step 3

生成新 baseline

## Step 4

恢复 A/B 门禁

---

# 8. Retrieval Intent Profile 审查结果

当前实现：

通过。

已验证：

## source-only 防护

不会：

```
source=StampTools

↓

直接提升
```

必须：

```
entity
+
intent
+
section
+
anchor
```

---

## 中性查询

通过。

不会：

```
普通问题

↓

被profile接管
```

---

## sibling penalty

通过。

不会：

```
目标族文档

↓

被兄弟族惩罚
```

---

# 9. 当前不需要修改的地方

不要：

* 增大 profile 权重
* 增加更多 source bias
* 关闭 Entity Guard
* 放宽 reranker
* 删除 fail-on-regression

这些都会掩盖真实问题。

---

# 10. 当前优先级

## P0

完成：

* eval dataset health check
* hardcases 重建
* chunk_id 解耦

## P1

完成：

* source/section/content fingerprint
* 自动生成 hardcases

## P2

增强：

* baseline version 管理
* 指标历史追踪
* 自动回归报告

---

# 11. 最终状态判断

当前系统：

```
数据层:
正常


检索层:
正常


Graph:
正常


Profile治理:
正常


测试:
正常


评估资产:
存在设计缺陷
```

真正剩余问题：

不是 RAG 检索能力。

而是：

```
知识库生命周期
        +
评测资产生命周期
没有绑定管理
```

修复这个以后，整个 RAG 治理闭环才完整。
