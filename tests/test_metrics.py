import unittest
from types import SimpleNamespace

from rag_knowledge.evaluation.metrics import compute_all
from rag_knowledge.evaluation.runner import EvaluationRunner


class MetricsTests(unittest.TestCase):
    def test_compute_all_includes_binary_ndcg(self):
        metrics = compute_all(["irrelevant", "relevant"], {"relevant"}, [3])
        self.assertIn("ndcg@3", metrics)
        self.assertGreater(metrics["ndcg@3"], 0.0)
        self.assertLess(metrics["ndcg@3"], 1.0)

    def test_content_fallback_does_not_change_recall_denominator(self):
        runner = object.__new__(EvaluationRunner)
        runner._dataset_path = "dummy.json"
        runner._review_status = "approved"
        runner._allow_stale_ids = False
        runner._dataset = [{
            "question": "如何启动服务？",
            "relevant_chunk_ids": ["gold-chunk"],
            "expected_targets": [{
                "source": "manual.md",
                "section_path": "部署 > 启动",
                "keywords": ["pm2"],
            }],
        }]
        runner._health_report = SimpleNamespace(to_dict=lambda: {"status": "PASS"})

        def fake_retrieve(question, kb_name=None, doc_category=None, review_status=None, method=None, rerank=None):
            return ([{
                "metadata": {
                    "chunk_id": "wrong-chunk",
                    "source": "manual.md",
                    "section_path": "部署 > 启动",
                },
                "content": "通过 pm2 启动服务",
            }], None)

        runner._rag = SimpleNamespace(_retrieve=fake_retrieve)
        metrics = runner.run_retrieval_eval(k_values=[3], verbose=False)

        self.assertEqual(metrics["recall@3"], 0.0)
        self.assertEqual(metrics["mrr"], 0.0)
        self.assertEqual(metrics["content_fallback_hit_rate"], 1.0)
        self.assertEqual(metrics["review_scope"], "approved")


if __name__ == "__main__":
    unittest.main()
