import unittest

from rag_knowledge.config import ContextBudgetConfig
from rag_knowledge.services.context_budget import ContextBudgetManager, _rebuild_context


def _doc(citation_id: int, content: str, quality_score=None):
    metadata = {
        "citation_id": citation_id,
        "file_name": f"doc-{citation_id}.md",
        "page_label": str(citation_id),
        "category": "text",
        "source_type": "knowledge_base",
    }
    if quality_score is not None:
        metadata["quality_score"] = quality_score
    return {"content": content, "metadata": metadata}


class ContextBudgetManagerTests(unittest.TestCase):
    def test_disabled_config_returns_inputs_unchanged(self):
        cfg = ContextBudgetConfig(enabled=False)
        manager = ContextBudgetManager(cfg)
        docs = [_doc(1, "alpha")]
        context = _rebuild_context(docs)
        history = [{"role": "user", "content": "hello"}]

        trimmed_docs, trimmed_context, trimmed_history = manager.trim(
            docs, context, history, "question"
        )

        self.assertIs(trimmed_docs, docs)
        self.assertEqual(trimmed_context, context)
        self.assertIs(trimmed_history, history)

    def test_history_is_trimmed_from_oldest_forward(self):
        cfg = ContextBudgetConfig(
            enabled=True,
            context_window=24,
            generation_reserve=0,
            system_reserve=0,
            question_reserve=0,
            context_ratio=0.5,
            chars_per_token=1.0,
        )
        manager = ContextBudgetManager(cfg)
        docs = [_doc(1, "ctx")]
        context = _rebuild_context(docs)
        history = [
            {"role": "user", "content": "111111"},
            {"role": "assistant", "content": "222222"},
            {"role": "user", "content": "333"},
        ]

        _, _, trimmed_history = manager.trim(docs, context, history, "q")

        self.assertEqual(
            trimmed_history,
            [
                {"role": "assistant", "content": "222222"},
                {"role": "user", "content": "333"},
            ],
        )

    def test_context_is_trimmed_by_lowest_quality_score_first(self):
        cfg = ContextBudgetConfig(
            enabled=True,
            context_window=150,
            generation_reserve=0,
            system_reserve=0,
            question_reserve=0,
            context_ratio=0.4,
            chars_per_token=1.0,
        )
        manager = ContextBudgetManager(cfg)
        docs = [
            _doc(1, "A" * 40, quality_score=0.9),
            _doc(2, "B" * 40, quality_score=0.1),
            _doc(3, "C" * 40, quality_score=0.6),
        ]
        context = _rebuild_context(docs)

        trimmed_docs, trimmed_context, _ = manager.trim(docs, context, None, "q")

        self.assertEqual([doc["metadata"]["citation_id"] for doc in trimmed_docs], [1])
        self.assertIn("A" * 40, trimmed_context)
        self.assertNotIn("B" * 40, trimmed_context)
        self.assertNotIn("C" * 40, trimmed_context)

    def test_missing_quality_score_is_treated_as_lowest_priority(self):
        cfg = ContextBudgetConfig(
            enabled=True,
            context_window=150,
            generation_reserve=0,
            system_reserve=0,
            question_reserve=0,
            context_ratio=0.4,
            chars_per_token=1.0,
        )
        manager = ContextBudgetManager(cfg)
        docs = [
            _doc(1, "A" * 40, quality_score=0.9),
            _doc(2, "B" * 40),
            _doc(3, "C" * 40, quality_score=0.2),
        ]
        context = _rebuild_context(docs)

        trimmed_docs, _, _ = manager.trim(docs, context, None, "q")

        self.assertEqual([doc["metadata"]["citation_id"] for doc in trimmed_docs], [1])

    def test_at_least_one_source_is_retained_even_when_over_budget(self):
        cfg = ContextBudgetConfig(
            enabled=True,
            context_window=120,
            generation_reserve=0,
            system_reserve=0,
            question_reserve=0,
            context_ratio=0.3,
            chars_per_token=1.0,
        )
        manager = ContextBudgetManager(cfg)
        docs = [
            _doc(1, "A" * 80, quality_score=0.8),
            _doc(2, "B" * 80, quality_score=0.1),
        ]
        context = _rebuild_context(docs)

        trimmed_docs, trimmed_context, _ = manager.trim(docs, context, None, "q")

        self.assertEqual(len(trimmed_docs), 1)
        self.assertEqual(trimmed_docs[0]["metadata"]["citation_id"], 1)
        self.assertEqual(trimmed_context, _rebuild_context(trimmed_docs))

    def test_tiny_budget_behavior_is_deterministic(self):
        cfg = ContextBudgetConfig(
            enabled=True,
            context_window=1,
            generation_reserve=0,
            system_reserve=0,
            question_reserve=0,
            context_ratio=0.5,
            chars_per_token=1.0,
        )
        manager = ContextBudgetManager(cfg)
        docs = [
            _doc(1, "A" * 20, quality_score=0.7),
            _doc(2, "B" * 20, quality_score=0.2),
        ]
        context = _rebuild_context(docs)
        history = [
            {"role": "user", "content": "111"},
            {"role": "assistant", "content": "222"},
        ]

        trimmed_docs, trimmed_context, trimmed_history = manager.trim(
            docs, context, history, "q"
        )

        self.assertEqual([doc["metadata"]["citation_id"] for doc in trimmed_docs], [1])
        self.assertEqual(trimmed_context, _rebuild_context(trimmed_docs))
        self.assertIsNone(trimmed_history)


if __name__ == "__main__":
    unittest.main()
