import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.evaluation.runner import EvaluationRunner
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.reranker import (
    CrossEncoderReranker,
    FlagReranker,
    create_reranker,
)


def _docs(count: int) -> list[Document]:
    return [
        Document(page_content=f"doc-{i}", metadata={"chunk_id": str(i)})
        for i in range(count)
    ]


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    isolated_storage(
        db_name="reranker.db",
        chroma_name="reranker-chroma",
        data_dir_name="reranker-data",
    )


class RerankerUnitTests(unittest.TestCase):
    def test_factory_types_and_unknown_type(self):
        self.assertIsInstance(create_reranker("bge", "org/model"), FlagReranker)
        self.assertIsInstance(
            create_reranker("cross_encoder", "org/model"), CrossEncoderReranker
        )
        with self.assertRaises(ValueError):
            create_reranker("unknown", "org/model")

    def test_flag_model_is_lazy_loaded(self):
        fake_model = MagicMock()
        fake_module = types.ModuleType("FlagEmbedding")
        fake_constructor = MagicMock(return_value=fake_model)
        fake_module.FlagReranker = fake_constructor

        reranker = FlagReranker("org/model")
        self.assertIsNone(reranker._model)
        with patch.dict(sys.modules, {"FlagEmbedding": fake_module}):
            fake_model.compute_score.return_value = [0.5]
            reranker.rerank("q", _docs(1), 1)
        fake_constructor.assert_called_once_with("org/model", use_fp16=True)

    def test_flag_ranks_scores_and_handles_single_float(self):
        reranker = FlagReranker("org/model")
        reranker._model = MagicMock()
        docs = _docs(3)
        reranker._model.compute_score.return_value = [0.2, 0.9, 0.5]
        result = reranker.rerank("q", docs, 2)
        self.assertEqual([d.metadata["chunk_id"] for d in result], ["1", "2"])

        reranker._model.compute_score.return_value = 0.7
        self.assertEqual(reranker.rerank("q", docs[:1], 1), docs[:1])

    def test_empty_input_and_non_positive_top_k_do_not_load(self):
        reranker = FlagReranker("org/model")
        self.assertEqual(reranker.rerank("q", [], 2), [])
        self.assertEqual(reranker.rerank("q", _docs(1), 0), [])
        self.assertIsNone(reranker._model)

    def test_explicit_incomplete_local_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "不完整"):
                create_reranker("bge", str(Path(directory).resolve()))


class RagRerankerIntegrationTests(unittest.TestCase):
    def _chain(self, docs):
        chain = object.__new__(RagChain)
        chain._reranker = None
        chain._reranker_enabled = False
        chain._reranker_type = "bge"
        chain._reranker_model = "org/model"
        chain._reranker_top_n = 2
        chain._reranker_candidate_k = 5
        chain._retrieval_k = 4
        chain._store = MagicMock()
        chain._strategy = MagicMock()
        chain._strategy.retrieve.return_value = docs
        chain._quality = MagicMock()
        chain._quality.apply.side_effect = lambda query, values, **kwargs: values
        return chain

    @patch("rag_knowledge.services.reranker.create_reranker")
    def test_force_rerank_does_not_create_when_config_disabled(self, create):
        docs = _docs(5)
        chain = self._chain(docs)

        sources, _ = chain._retrieve("q", kb_name="kb", method="hybrid", rerank=True)

        create.assert_not_called()
        self.assertEqual([d["metadata"]["chunk_id"] for d in sources], ["0", "1"])
        self.assertEqual(chain._strategy.retrieve.call_args.kwargs["top_k"], 5)

    @patch("rag_knowledge.services.reranker.create_reranker")
    def test_enabled_rerank_creates_and_calls(self, create):
        docs = _docs(5)
        reranker = MagicMock()
        reranker.rerank.return_value = [docs[4], docs[3]]
        create.return_value = reranker
        chain = self._chain(docs)
        chain._reranker_enabled = True

        sources, _ = chain._retrieve("q", kb_name="kb", method="hybrid", rerank=True)

        create.assert_called_once_with("bge", "org/model")
        reranker.rerank.assert_called_once_with("q", docs, 2)
        self.assertEqual([d["metadata"]["chunk_id"] for d in sources], ["4", "3"])
        self.assertEqual(chain._strategy.retrieve.call_args.kwargs["top_k"], 5)

    @patch("rag_knowledge.services.reranker.create_reranker")
    def test_explicit_false_does_not_create_reranker(self, create):
        chain = self._chain(_docs(3))
        chain._retrieve("q", kb_name="kb", method="hybrid", rerank=False)
        create.assert_not_called()
        self.assertIsNone(chain._strategy.retrieve.call_args.kwargs["top_k"])

    def test_retrieve_emits_stage_diagnostics_without_changing_results(self):
        docs = _docs(3)
        chain = self._chain(docs)
        diagnostics = {}

        sources, _ = chain._retrieve(
            "q", kb_name="kb", method="hybrid", rerank=False, diagnostics=diagnostics
        )

        self.assertEqual([d["metadata"]["chunk_id"] for d in sources], ["0", "1", "2"])
        self.assertEqual(
            list(diagnostics), ["retrieved", "post_rerank", "post_quality", "final"]
        )
        self.assertEqual(
            [d.metadata["chunk_id"] for d in diagnostics["final"]], ["0", "1", "2"]
        )

    def test_evaluation_retrieval_reuses_production_query_plan(self):
        chain = self._chain(_docs(2))
        plan = SimpleNamespace(
            queries=["q"], enable_rerank=False, top_k=4, candidate_k=5,
            expand_neighbors=False, intent_plan=None,
        )
        chain._build_retrieval_query_specs = MagicMock(return_value=plan.queries)
        chain._plan_retrieval = MagicMock(return_value=plan)
        chain._prepare_graph_plan = MagicMock(return_value=(plan, None, None))
        chain._build_graph_kwargs = MagicMock(return_value={})

        sources, _ = chain.retrieve_for_evaluation("q", diagnostics={})

        self.assertEqual([item["metadata"]["chunk_id"] for item in sources], ["0", "1"])
        chain._plan_retrieval.assert_called_once_with("q", plan.queries, force_rerank=True)

    @patch("rag_knowledge.services.reranker.create_reranker", side_effect=RuntimeError("offline"))
    def test_initialization_failure_falls_back_to_original_top_n(self, create):
        chain = self._chain(_docs(5))
        chain._reranker_enabled = True
        sources, _ = chain._retrieve("q", kb_name="kb", rerank=True)
        self.assertEqual([d["metadata"]["chunk_id"] for d in sources], ["0", "1"])

    @patch("rag_knowledge.services.reranker.create_reranker")
    def test_postprocess_sync_none_reranker_truncates_without_create(self, create):
        chain = self._chain(_docs(5))
        chain._reranker_enabled = False
        docs = chain._postprocess_docs_sync("q", _docs(5), enable_rerank=True, target_top_k=2)
        create.assert_not_called()
        self.assertEqual(len(docs), 2)
        self.assertEqual([d.metadata["chunk_id"] for d in docs], ["0", "1"])

    @patch("rag_knowledge.services.reranker.create_reranker")
    def test_postprocess_async_none_reranker_truncates_without_create(self, create):
        import asyncio

        chain = self._chain(_docs(5))
        chain._reranker_enabled = False
        docs = asyncio.run(
            chain._postprocess_docs("q", _docs(5), enable_rerank=True, target_top_k=2)
        )
        create.assert_not_called()
        self.assertEqual(len(docs), 2)
        self.assertEqual([d.metadata["chunk_id"] for d in docs], ["0", "1"])


class RerankerEvaluationTests(unittest.TestCase):
    def test_ablation_restores_global_quality_config_after_failure(self):
        runner = object.__new__(EvaluationRunner)
        cfg = Config()
        original = cfg.retrieval_quality.enabled
        self.addCleanup(setattr, cfg.retrieval_quality, "enabled", original)
        cfg.retrieval_quality.enabled = True
        runner.run_retrieval_eval = MagicMock(side_effect=RuntimeError("evaluation failed"))

        with self.assertRaisesRegex(RuntimeError, "evaluation failed"):
            runner.run_ablation(methods=["hybrid"], k_values=[3])

        self.assertTrue(cfg.retrieval_quality.enabled)

    def test_ablation_disables_quality_for_baseline_and_enables_variant(self):
        runner = object.__new__(EvaluationRunner)
        states = []

        def capture(**kwargs):
            states.append(Config().retrieval_quality.enabled)
            return {"mrr": 1.0}

        runner.run_retrieval_eval = MagicMock(side_effect=capture)
        runner.run_ablation(
            methods=["hybrid", "hybrid+quality"], k_values=[3]
        )
        self.assertEqual(states, [False, True])

    def test_ablation_maps_hybrid_rerank_to_forced_reranking(self):
        runner = object.__new__(EvaluationRunner)
        runner.run_retrieval_eval = MagicMock(return_value={"mrr": 1.0})
        results = runner.run_ablation(methods=["hybrid+rerank"], k_values=[3, 5])
        runner.run_retrieval_eval.assert_called_once_with(
            k_values=[3, 5], verbose=True, method="hybrid", rerank=True
        )
        self.assertEqual(results[0]["method"], "hybrid+rerank")

    def test_full_eval_script_contains_rerank_and_direct_comparison(self):
        script = Path("run_eval_full.py").read_text(encoding="utf-8")
        self.assertIn('"hybrid+rerank"', script)
        self.assertIn("Hybrid → Hybrid+Rerank 直接对比", script)
        self.assertIn('"recall@5"', script)


if __name__ == "__main__":
    unittest.main()
