import json
import unittest
import pytest
from pathlib import Path
from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy
from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.query_cache import clear_query_cache
from rag_knowledge.services.rag import RagChain

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _setup_regression_env(test_case):
    """Common test setup returning (chroma_client, count). Handles skips if DB empty."""
    Config._instance = None
    VectorStore._instance = None
    BM25Store._instance = None

    store = VectorStore()
    try:
        chroma = store.get_chroma()
        count = chroma._collection.count()
    except Exception as e:
        test_case.skipTest(f"Chroma connection failed: {e}")

    if count == 0:
        test_case.skipTest("Chroma database is empty, skipping integration regression test.")

    BM25Store().rebuild()
    clear_query_cache()
    return chroma, count


@pytest.mark.integration
class RetrievalRegressionIntegrationTests(unittest.TestCase):

    def _run_regression_for_status(self, review_status):
        dataset_path = PROJECT_ROOT / "data/structured_retrieval_regression.json"
        self.assertTrue(dataset_path.exists())

        with open(dataset_path, "r", encoding="utf-8") as f:
            cases = json.load(f)

        cfg = Config()
        strategy = RetrievalStrategy()
        quality = RetrievalQualityStrategy(cfg)

        failures = []
        for case in cases:
            question = case["question"]
            expected_list = case["expected"]

            # Table-oriented queries require a larger pool to allow quality boosting to rank them higher
            is_table = any(hint in question for hint in ("规范", "要求", "字段", "表结构", "点表", "线表", "数据结构"))
            retrieval_k = 12 if is_table else 4
            docs = strategy.retrieve(question, review_status=review_status, top_k=retrieval_k)
            docs = quality.apply(question, docs)

            top3 = docs[:3]

            for expected in expected_list:
                matched = False
                expected_source = expected.get("source")
                expected_section = expected.get("section_path_contains")
                must_include = expected.get("content_must_include", [])

                for doc in top3:
                    meta = doc.metadata or {}
                    actual_source = meta.get("source") or meta.get("file_name") or ""
                    if expected_source and expected_source != actual_source:
                        continue

                    actual_section = meta.get("section_path", "")
                    if expected_section and expected_section not in actual_section:
                        continue

                    content = doc.page_content or ""
                    if not all(term in content for term in must_include):
                        continue

                    matched = True
                    break

                if not matched:
                    top3_info = []
                    for idx, doc in enumerate(top3):
                        m = doc.metadata or {}
                        top3_info.append(
                            f"  Rank {idx+1}: source='{m.get('source') or m.get('file_name')}', "
                            f"section_path='{m.get('section_path')}', content_preview='{doc.page_content[:80].replace(chr(10), ' ')}...'"
                        )
                    failures.append(
                        f"Question: '{question}'\n"
                        f"Expected: source='{expected_source}', section_path_contains='{expected_section}', must_include={must_include}\n"
                        f"Top 3 retrieved docs:\n" + "\n".join(top3_info)
                    )

        if failures:
            self.fail(f"Regression failures (review_status={review_status}):\n\n" + "\n\n".join(failures))

    def test_structured_retrieval_regression_cases_diagnostic(self):
        """Diagnostic regression run (review_status=None) covering all documents."""
        _setup_regression_env(self)
        self._run_regression_for_status(review_status=None)

    def test_structured_retrieval_regression_cases_prod(self):
        """Production regression run (review_status='approved') verifying only approved documents."""
        chroma, _ = _setup_regression_env(self)

        # Check if there are any approved documents in the database
        res = chroma._collection.get(where={"review_status": "approved"}, limit=1)
        if not res.get("ids", []):
            self.skipTest("No approved documents in Chroma database. Skipping production regression test.")

        self._run_regression_for_status(review_status="approved")


@pytest.mark.integration
class RagChainRetrievalRegressionTests(unittest.TestCase):

    def test_manual_queries_routing(self):
        """Verify routing heuristics in RagChain."""
        _setup_regression_env(self)
        chain = RagChain()
        self.assertEqual(chain._route_query("管线点表规范"), "文章附件")
        self.assertEqual(chain._route_query("PipelineBuilder 管线点表字段要求"), "文章附件")
        self.assertEqual(chain._route_query("管线线表字段"), "文章附件")
        self.assertEqual(chain._route_query("DOMBuilder"), "文章附件")

    def _run_rag_chain_retrieval_for_status(self, review_status):
        dataset_path = PROJECT_ROOT / "data/structured_retrieval_regression.json"
        self.assertTrue(dataset_path.exists())

        with open(dataset_path, "r", encoding="utf-8") as f:
            cases = json.load(f)

        chain = RagChain()
        failures = []
        for case in cases:
            question = case["question"]
            expected_list = case["expected"]

            # For table-oriented queries, retrieve candidate pool of 12
            is_table = any(hint in question for hint in ("规范", "要求", "字段", "表结构", "点表", "线表", "数据结构"))
            retrieval_k = 12 if is_table else 4

            # Query via RagChain._retrieve pipeline
            source_docs, _ = chain._retrieve(question, review_status=review_status, top_k_override=retrieval_k)
            top3 = source_docs[:3]

            for expected in expected_list:
                matched = False
                expected_source = expected.get("source")
                expected_section = expected.get("section_path_contains")
                must_include = expected.get("content_must_include", [])

                for doc in top3:
                    meta = doc.get("metadata", {})
                    actual_source = meta.get("source") or meta.get("file_name") or ""
                    if expected_source and expected_source != actual_source:
                        continue

                    actual_section = meta.get("section_path", "")
                    if expected_section and expected_section not in actual_section:
                        continue

                    content = doc.get("content", "")
                    if not all(term in content for term in must_include):
                        continue

                    matched = True
                    break

                if not matched:
                    top3_info = []
                    for idx, doc in enumerate(top3):
                        meta = doc.get("metadata", {})
                        top3_info.append(
                            f"  Rank {idx+1}: source='{meta.get('source') or meta.get('file_name')}', "
                            f"section_path='{meta.get('section_path')}', content_preview='{doc.get('content', '')[:80].replace(chr(10), ' ')}...'"
                        )
                    failures.append(
                        f"Question: '{question}'\n"
                        f"Expected: source='{expected_source}', section_path_contains='{expected_section}', must_include={must_include}\n"
                        f"Top 3 retrieved docs:\n" + "\n".join(top3_info)
                    )

        if failures:
            self.fail(f"RagChain regression failures (review_status={review_status}):\n\n" + "\n\n".join(failures))

    def test_rag_chain_retrieve_pipeline_diagnostic(self):
        """Verifies full retrieval flow via RagChain with review_status=None."""
        _setup_regression_env(self)
        self._run_rag_chain_retrieval_for_status(review_status=None)

    def test_rag_chain_retrieve_pipeline_prod(self):
        """Verifies full retrieval flow via RagChain with review_status='approved'."""
        chroma, _ = _setup_regression_env(self)

        res = chroma._collection.get(where={"review_status": "approved"}, limit=1)
        if not res.get("ids", []):
            self.skipTest("No approved documents in Chroma database. Skipping production regression test.")

        self._run_rag_chain_retrieval_for_status(review_status="approved")


if __name__ == "__main__":
    unittest.main()
