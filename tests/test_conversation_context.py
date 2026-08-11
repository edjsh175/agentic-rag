"""Phase 0–2：对话上下文契约与 GenerationPack / DialogueFocus。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from rag_knowledge.config import Config, ContextBudgetConfig, HistoryCompressionConfig
from rag_knowledge.services.context_budget import ContextBudgetManager
from rag_knowledge.services.conversation_context import (
    DialogueFocus,
    GenerationPack,
    build_dialogue_focus,
    extract_source_summaries,
    format_retrieval_memory,
    session_from_history,
)
from rag_knowledge.services.history_compressor import HistoryCompressor


def _doc(citation_id: int, content: str, quality_score=0.5):
    return {
        "content": content,
        "metadata": {
            "citation_id": citation_id,
            "file_name": f"doc-{citation_id}.md",
            "page_label": str(citation_id),
            "category": "text",
            "source_type": "knowledge_base",
            "quality_score": quality_score,
            "chunk_id": f"c{citation_id}",
        },
    }


class SessionAdapterTests(unittest.TestCase):
    def test_session_from_history_keeps_last_sources(self):
        history = [
            {"role": "user", "content": "q1"},
            {
                "role": "assistant",
                "content": "a1",
                "sources": [{"file_name": "a.md", "chunk_id": "1"}],
            },
            {"role": "user", "content": "继续说"},
        ]
        session = session_from_history(
            history, entity_name="StampServer", doc_category="StampServer"
        )
        self.assertEqual(len(session.turns), 3)
        self.assertEqual(session.last_sources[0]["chunk_id"], "1")
        self.assertEqual(session.resolved_entity, "StampServer")
        exported = session.to_history()
        self.assertIn("sources", exported[1])
        self.assertEqual(exported[1]["sources"][0]["file_name"], "a.md")


class DialogueFocusTests(unittest.TestCase):
    def test_build_focus_from_session_and_entity(self):
        history = [
            {"role": "user", "content": "PipelineBuilder 怎么用"},
            {
                "role": "assistant",
                "content": "介绍",
                "sources": [{"file_name": "pb.md", "section_title": "安装"}],
            },
        ]
        session = session_from_history(history, entity_name="PipelineBuilder")
        focus = build_dialogue_focus(
            "继续说", session, resolved_question="PipelineBuilder 继续说明",
        )
        self.assertEqual(focus.confirmed_entity, "PipelineBuilder")
        self.assertEqual(focus.topic, "PipelineBuilder 怎么用")
        self.assertIn("PipelineBuilder", focus.to_text())
        self.assertIn("实体:", focus.to_text())

    def test_format_retrieval_memory_is_short(self):
        history = [
            {"role": role, "content": f"long-content-{i}-" + ("x" * 200)}
            for i in range(6)
            for role in ("user", "assistant")
        ]
        text = format_retrieval_memory(
            history,
            focus_text="实体:StampServer | 焦点:它是什么",
            rolling_summary="主题：服务\n实体：StampServer\n结论：已介绍\n未决：\n",
            recent_rounds=2,
            content_chars=40,
        )
        self.assertIn("对话焦点：", text)
        self.assertIn("历史摘要：", text)
        self.assertIn("最近对话：", text)
        self.assertNotIn("long-content-0", text)
        self.assertIn("long-content-4", text)
        self.assertLess(len(text), 900)

    def test_from_dict_roundtrip(self):
        focus = DialogueFocus.from_dict(
            {"topic": "t", "confirmed_entity": "e", "open_question": "q", "notes": "n"}
        )
        self.assertEqual(focus.to_dict()["confirmed_entity"], "e")


class SourceSummaryTests(unittest.TestCase):
    def test_extract_source_summaries_fields(self):
        docs = [_doc(1, "hello world " * 30)]
        summaries = extract_source_summaries(docs)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["file_name"], "doc-1.md")
        self.assertEqual(summaries[0]["chunk_id"], "c1")
        self.assertEqual(summaries[0]["citation_id"], "1")
        self.assertLessEqual(len(summaries[0]["preview"]), 200)


class GenerationPackTests(unittest.TestCase):
    def setUp(self):
        self.budget = ContextBudgetManager(
            ContextBudgetConfig(
                enabled=True,
                context_window=10_000,
                generation_reserve=0,
                system_reserve=0,
                question_reserve=0,
                context_ratio=0.7,
                chars_per_token=1.0,
            )
        )
        self.main_cfg = MagicMock(spec=Config)
        self.compressor = HistoryCompressor(
            HistoryCompressionConfig(
                enabled=True, min_raw_rounds=2, max_raw_rounds=4
            ),
            self.main_cfg,
        )
        self.pack = GenerationPack(self.compressor, self.budget)

    def test_pack_truncates_on_cache_miss(self):
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        self.compressor._generate_summary = MagicMock(return_value="summary")
        docs = [_doc(1, "ctx")]
        from rag_knowledge.services.context_budget import _rebuild_context

        result = self.pack.pack(docs, _rebuild_context(docs), history, "q")
        self.assertEqual(result.decision.compress_fallback, "truncate_recent")
        self.assertFalse(result.decision.used_summary)
        self.assertEqual(len(result.history or []), 4)
        self.assertTrue(result.decision.scheduled_background_summary)

    def test_pack_uses_summary_cache(self):
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        older = history[:-4]
        key = self.compressor._hash_history(older)
        self.compressor._cache_put(key, "cached-summary")
        docs = [_doc(1, "ctx")]
        from rag_knowledge.services.context_budget import _rebuild_context

        result = self.pack.pack(docs, _rebuild_context(docs), history, "q")
        self.assertEqual(result.decision.compress_fallback, "summary_cache")
        self.assertTrue(result.decision.used_summary)
        self.assertEqual(result.history_summary, "cached-summary")
        self.assertEqual(len(result.history or []), 4)


if __name__ == "__main__":
    unittest.main()
