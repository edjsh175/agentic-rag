import unittest
from unittest.mock import MagicMock

from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.services.query_planner import QueryPlanner


class QueryPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = QueryPlanner()
        self.planner._planner_cfg.enabled = True
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
        plan = self.planner.plan(
            "DOMBuilder config types",
            [RetrievalQuery("DOMBuilder config types", "original", 1.0)],
            force_rerank=True,
        )

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
