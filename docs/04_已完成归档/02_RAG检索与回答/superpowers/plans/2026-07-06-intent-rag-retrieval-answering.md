# Intent-RAG Retrieval And Partial-Answering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the intent-driven retrieval planner rollout and make partial-hit answers keep their cited knowledge-base sources across sync, async, and streaming RAG flows.

**Architecture:** Keep the existing `QueryPlanner -> retrieval -> postprocess -> answer` structure, then tighten the seams where plan parameters, neighbor expansion, and citation filtering can drift between code paths. Use tests first to lock in planner behavior and partial-answer source retention before changing implementation details.

**Tech Stack:** Python, unittest/pytest, LangChain documents, local Ollama-backed RAG services.

---

### Task 1: Lock Partial-Answer Source Retention With Tests

**Files:**
- Modify: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_prompt_engineering.py`
- Test: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_prompt_engineering.py`

- [ ] **Step 1: Write the failing test**

```python
def test_partial_answer_keeps_cited_sources(self):
    sources = [
        {"content": "alpha", "metadata": {"citation_id": 1}},
        {"content": "beta", "metadata": {"citation_id": 2}},
    ]

    answer = (
        "已查到部署准备步骤 [1]。"
        "以上为知识库中已查到的部分内容。"
        "关于发布回滚，当前知识库中未查询到相关内容。"
    )

    trusted = RagChain._filter_cited_sources(answer, sources)

    self.assertEqual(
        [source["metadata"]["citation_id"] for source in trusted], [1]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prompt_engineering.py -k partial_answer_keeps_cited_sources -q`
Expected: FAIL if partial-answer text is treated like the exact no-knowledge fallback or citations are dropped.

- [ ] **Step 3: Write minimal implementation**

```python
if not answer or not source_docs:
    return []
if answer.strip() == NO_KNOWLEDGE_ANSWER:
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prompt_engineering.py -k partial_answer_keeps_cited_sources -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_prompt_engineering.py rag_knowledge/services/rag.py
git commit -m "test: lock partial-answer citation retention"
```

### Task 2: Lock Planner Retrieval Defaults And Fallbacks

**Files:**
- Modify: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_query_planner.py`
- Test: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_query_planner.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_disabled_planner_returns_default_plan(self):
    self.planner._planner_cfg.enabled = False

    plan = self.planner.plan("DOMBuilder 的工程类型有哪些？")

    self.assertEqual(plan.intent, "definition")
    self.assertEqual(plan.top_k, self.planner._cfg.retrieval_top_k)
    self.assertFalse(plan.expand_neighbors)

def test_force_rerank_is_preserved_for_default_intent(self):
    plan = self.planner.plan("DOMBuilder 的工程类型有哪些？", force_rerank=True)

    self.assertTrue(plan.enable_rerank)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_query_planner.py -q`
Expected: FAIL if default-plan behavior or rerank override is inconsistent.

- [ ] **Step 3: Write minimal implementation**

```python
def _default_plan(...):
    return RetrievalPlan(
        intent="definition",
        queries=base_queries,
        top_k=self._cfg.retrieval_top_k,
        candidate_k=self._cfg.retrieval_candidate_k,
        enable_rerank=force_rerank,
        expand_neighbors=False,
        confidence=1.0,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_query_planner.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_query_planner.py rag_knowledge/services/query_planner.py
git commit -m "test: cover planner defaults and rerank fallback"
```

### Task 3: Connect Retrieval Plan And Partial-Answer Prompting

**Files:**
- Modify: `E:\申浩霖实习文件夹\rag_cy\rag\rag_knowledge\services\rag.py`
- Modify: `E:\申浩霖实习文件夹\rag_cy\rag\config.ini.example`
- Test: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_prompt_engineering.py`

- [ ] **Step 1: Implement the prompt and filtering changes**

```python
_SYSTEM_PROMPT = """...
3. context 仅能支持部分答案时，先回答有明确依据的部分，并在每项事实后引用编号；
   然后说明“以上为知识库中已查到的部分内容。关于[未覆盖方面]，当前知识库中未查询到相关内容。”
..."""

@staticmethod
def _filter_cited_sources(answer: str, source_docs: list[dict]) -> list[dict]:
    if not answer or not source_docs:
        return []
    if answer.strip() == NO_KNOWLEDGE_ANSWER:
        return []
```

- [ ] **Step 2: Run targeted tests**

Run: `python -m pytest tests/test_prompt_engineering.py tests/test_query_planner.py tests/test_neighbor_expansion.py -q`
Expected: PASS

- [ ] **Step 3: Mirror the planner-related sample config**

```ini
[query_planner]
enabled = true
llm_timeout = 15
procedure_top_k = 8
procedure_candidate_k = 24
troubleshooting_top_k = 6
troubleshooting_candidate_k = 18
comparison_top_k = 6
comparison_candidate_k = 18
max_expanded_queries = 8
neighbor_window = 2
max_neighbors_per_source = 6
```

- [ ] **Step 4: Run broader regression checks**

Run: `python -m pytest tests/test_prompt_engineering.py tests/test_query_planner.py tests/test_neighbor_expansion.py tests/test_rag_stage6.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add rag_knowledge/services/rag.py config.ini.example tests/test_prompt_engineering.py tests/test_query_planner.py
git commit -m "feat: finish intent planner and partial-answer source flow"
```

### Task 4: Verify End-To-End Retrieval Plan Propagation

**Files:**
- Modify: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_rag_stage6.py`
- Test: `E:\申浩霖实习文件夹\rag_cy\rag\tests\test_rag_stage6.py`

- [ ] **Step 1: Write the failing test**

```python
def test_query_uses_planner_parameters_for_retrieval(self):
    plan = RetrievalPlan(
        intent="procedure",
        queries=[RetrievalQuery("DOMBuilder 发布", "planner_stage", 0.45)],
        top_k=8,
        candidate_k=24,
        enable_rerank=True,
        expand_neighbors=True,
        confidence=0.9,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rag_stage6.py -k planner_parameters -q`
Expected: FAIL if one of the RAG entrypoints drops `plan_top_k`, `plan_candidate_k`, or `expand_neighbors`.

- [ ] **Step 3: Write minimal implementation**

```python
source_docs, context = self._retrieve_multi(
    plan.queries,
    ...,
    plan_top_k=plan.top_k,
    plan_candidate_k=plan.candidate_k,
    expand_neighbors=plan.expand_neighbors,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_rag_stage6.py -k planner_parameters -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_rag_stage6.py rag_knowledge/services/rag.py
git commit -m "test: verify retrieval plan propagation"
```
