import unittest
from unittest.mock import MagicMock

import pytest

from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.services.query_planner import QueryPlanner


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    isolated_storage(
        db_name="query-planner.db",
        chroma_name="query-planner-chroma",
        data_dir_name="query-planner-data",
    )


class QueryPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = QueryPlanner()
        self.planner._planner_cfg.enabled = True
        self.planner._cfg.reranker_enabled = True
        self.planner._classify_via_llm = MagicMock(
            side_effect=RuntimeError("LLM disabled in unit tests")
        )

    def test_procedure_question_expands_stage_queries(self):
        self.planner._classify_via_llm = MagicMock(return_value=("procedure", 0.95))

        plan = self.planner.plan(
            "How to publish with DOMBuilder?",
            [RetrievalQuery("How to publish with DOMBuilder?", "original", 1.0)],
        )

        self.assertEqual(plan.intent, "procedure")
        self.assertEqual(plan.top_k, self.planner._cfg.query_planner.procedure_top_k)
        self.assertEqual(
            plan.candidate_k, self.planner._cfg.query_planner.procedure_candidate_k
        )
        self.assertTrue(plan.enable_rerank)

        query_text = " ".join(q.text for q in plan.queries)
        self.assertIn("How to publish", query_text)
        self.assertGreater(len(plan.queries), 1)

    def test_deployment_question_uses_deployment_intent(self):
        self.planner._classify_via_llm = MagicMock(return_value=("deployment", 0.95))

        plan = self.planner.plan(
            "How to deploy webrtc?",
            [RetrievalQuery("How to deploy webrtc?", "original", 1.0)],
        )

        self.assertEqual(plan.intent, "deployment")
        self.assertTrue(plan.enable_rerank)
        self.assertTrue(plan.expand_neighbors)
        self.assertIn("webrtc", " ".join(q.text for q in plan.queries))

    def test_config_question_keeps_default_retrieval_size(self):
        self.planner._cfg.reranker_enabled = True
        plan = self.planner.plan(
            "DOMBuilder config types",
            [RetrievalQuery("DOMBuilder config types", "original", 1.0)],
        )

        self.assertIn(plan.intent, {"definition", "config"})
        self.assertEqual(plan.top_k, self.planner._cfg.retrieval_top_k)
        self.assertEqual(plan.candidate_k, self.planner._cfg.retrieval_candidate_k)
        self.assertTrue(plan.enable_rerank)

    def test_conflicting_port_question_adds_evidence_scope_queries(self):
        self.planner._classify_via_llm = MagicMock(return_value=("config", 0.95))

        plan = self.planner.plan(
            "Turnserver 的 TLS 监听端口是多少？如果出现多个值应如何处理？",
            [
                RetrievalQuery(
                    "Turnserver 的 TLS 监听端口是多少？如果出现多个值应如何处理？",
                    "original",
                    1.0,
                )
            ],
        )

        conflict_queries = [
            query for query in plan.queries if query.kind == "planner_conflict"
        ]
        self.assertEqual(
            [query.text for query in conflict_queries],
            ["Turnserver TLS 端口说明", "Turnserver TLS 端口配置"],
        )
        self.assertTrue(all(query.weight == 0.6 for query in conflict_queries))
        self.assertEqual(plan.top_k, max(self.planner._cfg.retrieval_top_k, 6))
        self.assertEqual(plan.candidate_k, max(self.planner._cfg.retrieval_candidate_k, 18))

    def test_explicit_port_values_add_evidence_scope_queries(self):
        self.planner._classify_via_llm = MagicMock(return_value=("config", 0.95))

        plan = self.planner.plan(
            "Turnserver TLS 端口在端口表与正文是否一致？出现 5439 与 5349 时应如何回答？"
        )

        self.assertIn("planner_conflict", [query.kind for query in plan.queries])
        self.assertEqual(plan.top_k, max(self.planner._cfg.retrieval_top_k, 6))

    def test_table_and_config_values_add_evidence_scope_queries(self):
        self.planner._classify_via_llm = MagicMock(return_value=("config", 0.95))

        plan = self.planner.plan("若表格写 5439、配置示例写 5349，能否只答其中一个？")

        self.assertIn("planner_conflict", [query.kind for query in plan.queries])

    def test_troubleshooting_question_uses_larger_candidate_pool(self):
        self.planner._classify_via_llm = MagicMock(
            return_value=("troubleshooting", 0.95)
        )

        plan = self.planner.plan(
            "DOMBuilder compile error",
            [RetrievalQuery("DOMBuilder compile error", "original", 1.0)],
        )

        self.assertEqual(plan.intent, "troubleshooting")
        self.assertEqual(
            plan.top_k, self.planner._cfg.query_planner.troubleshooting_top_k
        )
        self.assertEqual(
            plan.candidate_k,
            self.planner._cfg.query_planner.troubleshooting_candidate_k,
        )
        self.assertTrue(plan.enable_rerank)

    def test_comparison_question_uses_comparison_intent(self):
        self.planner._classify_via_llm = MagicMock(return_value=("comparison", 0.95))

        plan = self.planner.plan(
            "DOMBuilder vs DEMBuilder",
            [RetrievalQuery("DOMBuilder vs DEMBuilder", "original", 1.0)],
        )

        self.assertEqual(plan.intent, "comparison")
        self.assertEqual(plan.top_k, self.planner._cfg.query_planner.comparison_top_k)
        self.assertTrue(plan.enable_rerank)

    def test_disabled_planner_returns_default_plan(self):
        self.planner._planner_cfg.enabled = False

        plan = self.planner.plan(
            "DOMBuilder config types",
            [RetrievalQuery("DOMBuilder config types", "original", 1.0)],
        )

        self.assertEqual(plan.intent, "definition")
        self.assertEqual(plan.top_k, self.planner._cfg.retrieval_top_k)
        self.assertEqual(plan.candidate_k, self.planner._cfg.retrieval_candidate_k)
        self.assertFalse(plan.expand_neighbors)

    def test_force_rerank_is_preserved_for_default_intent(self):
        self.planner._cfg.reranker_enabled = True
        plan = self.planner.plan(
            "DOMBuilder config types",
            [RetrievalQuery("DOMBuilder config types", "original", 1.0)],
            force_rerank=True,
        )

        self.assertTrue(plan.enable_rerank)

    def test_force_rerank_blocked_when_reranker_disabled(self):
        self.planner._cfg.reranker_enabled = False
        plan = self.planner.plan(
            "DOMBuilder config types",
            [RetrievalQuery("DOMBuilder config types", "original", 1.0)],
            force_rerank=True,
        )

        self.assertFalse(plan.enable_rerank)


class QueryPlannerRerankGateTests(unittest.TestCase):
    def setUp(self):
        self.planner = QueryPlanner()
        self.planner._planner_cfg.enabled = True
        self.planner._classify_via_llm = MagicMock(
            side_effect=RuntimeError("LLM disabled in unit tests")
        )

    def _plan(self, intent: str, *, force_rerank: bool = False):
        self.planner._classify_via_llm = MagicMock(return_value=(intent, 0.95))
        return self.planner.plan(
            f"question for {intent}",
            [RetrievalQuery(f"question for {intent}", "original", 1.0)],
            force_rerank=force_rerank,
        )

    def test_disabled_reranker_blocks_all_intents(self):
        self.planner._cfg.reranker_enabled = False
        for intent in ("definition", "procedure", "troubleshooting", "comparison"):
            with self.subTest(intent=intent):
                plan = self._plan(intent, force_rerank=False)
                self.assertFalse(plan.enable_rerank)

    def test_disabled_reranker_blocks_force_rerank(self):
        self.planner._cfg.reranker_enabled = False
        for intent in ("definition", "procedure"):
            with self.subTest(intent=intent):
                plan = self._plan(intent, force_rerank=True)
                self.assertFalse(plan.enable_rerank)

    def test_enabled_reranker_allows_force_rerank_for_definition(self):
        self.planner._cfg.reranker_enabled = True
        plan = self._plan("definition", force_rerank=True)
        self.assertTrue(plan.enable_rerank)

    def test_enabled_reranker_allows_intent_rerank_without_force(self):
        self.planner._cfg.reranker_enabled = True
        plan = self._plan("procedure", force_rerank=False)
        self.assertTrue(plan.enable_rerank)

    def test_plan_exposes_empty_graph_fields_before_phase_c_enrichment(self):
        plan = self.planner.plan("DOMBuilder config types")

        self.assertEqual(plan.linked_entities, ())
        self.assertEqual(plan.graph_queries, ())
        self.assertEqual(plan.graph_chunk_ids, ())
        self.assertEqual(plan.excluded_entity_ids, ())
        self.assertEqual(plan.graph_revision, "")
        self.assertIsNone(plan.graph_fallback_reason)


if __name__ == "__main__":
    unittest.main()
