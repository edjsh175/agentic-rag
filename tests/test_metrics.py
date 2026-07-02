import unittest

from rag_knowledge.evaluation.metrics import compute_all


class MetricsTests(unittest.TestCase):
    def test_compute_all_includes_binary_ndcg(self):
        metrics = compute_all(["irrelevant", "relevant"], {"relevant"}, [3])
        self.assertIn("ndcg@3", metrics)
        self.assertGreater(metrics["ndcg@3"], 0.0)
        self.assertLess(metrics["ndcg@3"], 1.0)


if __name__ == "__main__":
    unittest.main()
