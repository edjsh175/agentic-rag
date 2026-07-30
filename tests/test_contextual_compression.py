import unittest
from unittest.mock import patch

from langchain_core.documents import Document

from rag_knowledge.config import RetrievalQualityConfig
from rag_knowledge.services.rag import RagChain


class _ResponseStub:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}}


class ContextualCompressionTests(unittest.TestCase):
    def _build_chain(self, enabled: bool = True, max_chars: int = 80) -> RagChain:
        chain = object.__new__(RagChain)
        chain._ollama_base = "http://localhost:11434"
        chain._cfg = None
        chain._retrieval_quality_cfg = RetrievalQualityConfig(
            contextual_compression_enabled=enabled,
            compression_model="compress-model",
            max_compressed_chunk_chars=max_chars,
            debug_log_enabled=False,
        )
        return chain

    def test_compression_disabled_keeps_original_chunk(self):
        chain = self._build_chain(enabled=False)
        docs = [Document(page_content="original chunk", metadata={"chunk_id": "c1"})]

        result = chain._compress_retrieved_docs("question", docs)

        self.assertEqual(result[0].page_content, "original chunk")
        self.assertEqual(result[0].metadata["chunk_id"], "c1")
        self.assertNotIn("compression_applied", result[0].metadata)

    def test_compression_enabled_replaces_content_and_preserves_metadata(self):
        chain = self._build_chain(enabled=True, max_chars=20)
        docs = [
            Document(
                page_content="original chunk with a lot of useful details",
                metadata={"chunk_id": "c1", "source": "manual.pdf", "page_number": 3},
            )
        ]

        with patch(
            "rag_knowledge.llm_http.chat_role",
            return_value="chunk with a lot of useful details",
        ):
            result = chain._compress_retrieved_docs("question", docs)

        self.assertEqual(result[0].page_content, "chunk with a lot of ")
        self.assertEqual(result[0].metadata["chunk_id"], "c1")
        self.assertEqual(result[0].metadata["source"], "manual.pdf")
        self.assertEqual(result[0].metadata["page_number"], 3)
        self.assertTrue(result[0].metadata["compression_applied"])
        self.assertEqual(
            result[0].metadata["raw_content_length"],
            len("original chunk with a lot of useful details"),
        )
        self.assertTrue(
            result[0].metadata["raw_content_preview"].startswith("original chunk")
        )

    def test_compression_failure_falls_back_to_original_chunk(self):
        chain = self._build_chain(enabled=True)
        docs = [Document(page_content="original chunk", metadata={"chunk_id": "c1"})]

        with patch(
            "rag_knowledge.llm_http.chat_role",
            side_effect=RuntimeError("compression failed"),
        ):
            result = chain._compress_retrieved_docs("question", docs)

        self.assertEqual(result[0].page_content, "original chunk")
        self.assertEqual(result[0].metadata["chunk_id"], "c1")
        self.assertFalse(result[0].metadata.get("compression_applied", False))

    def test_empty_compression_result_falls_back_to_original_chunk(self):
        chain = self._build_chain(enabled=True)
        docs = [Document(page_content="original chunk", metadata={"chunk_id": "c1"})]

        with patch(
            "rag_knowledge.llm_http.chat_role",
            return_value="   ",
        ):
            result = chain._compress_retrieved_docs("question", docs)

        self.assertEqual(result[0].page_content, "original chunk")
        self.assertFalse(result[0].metadata.get("compression_applied", False))

    def test_rewritten_compression_result_falls_back_to_original_chunk(self):
        chain = self._build_chain(enabled=True)
        docs = [Document(page_content="the original factual passage", metadata={"chunk_id": "c1"})]

        with patch(
            "rag_knowledge.llm_http.chat_role",
            return_value="a model-written summary",
        ):
            result = chain._compress_retrieved_docs("question", docs)

        self.assertEqual(result[0].page_content, "the original factual passage")
        self.assertFalse(result[0].metadata.get("compression_applied", False))

    def test_too_short_compression_result_falls_back_to_original_chunk(self):
        chain = self._build_chain(enabled=True)
        docs = [Document(page_content="original chunk", metadata={"chunk_id": "c1"})]

        with patch(
            "rag_knowledge.llm_http.chat_role",
            return_value="o",
        ):
            result = chain._compress_retrieved_docs("question", docs)

        self.assertEqual(result[0].page_content, "original chunk")
        self.assertFalse(result[0].metadata.get("compression_applied", False))


class RetrieveOrderingTests(unittest.TestCase):
    def test_retrieve_runs_quality_before_compression_and_formats_compressed_output(self):
        chain = object.__new__(RagChain)
        chain._retrieval_k = 5
        chain._retrieval_fetch_k = 20
        chain._retrieval_lambda = 0.7
        chain._reranker_candidate_k = 20
        chain._reranker_top_n = 4
        chain._store = None
        chain._route_query = lambda question: "kb"
        captured = {}

        original_docs = [
            Document(page_content="raw chunk a", metadata={"chunk_id": "a", "source": "a.md"}),
            Document(page_content="raw chunk b", metadata={"chunk_id": "b", "source": "b.md"}),
        ]

        chain._strategy = type(
            "StrategyStub",
            (),
            {"retrieve": lambda self, *args, **kwargs: original_docs},
        )()

        def apply_quality(query, docs, **kwargs):
            captured["quality_input"] = [doc.page_content for doc in docs]
            return [Document(page_content="after quality", metadata=docs[0].metadata)]

        chain._quality = type("QualityStub", (), {"apply": staticmethod(apply_quality)})()

        def compress_docs(query, docs):
            captured["compression_input"] = [doc.page_content for doc in docs]
            return [
                Document(
                    page_content="compressed snippet",
                    metadata={**docs[0].metadata, "compression_applied": True},
                )
            ]

        chain._compress_retrieved_docs = compress_docs

        source_docs, context = chain._retrieve("question", kb_name="kb", rerank=False)

        self.assertEqual(captured["quality_input"], ["raw chunk a", "raw chunk b"])
        self.assertEqual(captured["compression_input"], ["after quality"])
        self.assertEqual(source_docs[0]["content"], "compressed snippet")
        self.assertTrue(source_docs[0]["metadata"]["compression_applied"])
        self.assertIn("compressed snippet", context)
        self.assertNotIn("after quality", context)


if __name__ == "__main__":
    unittest.main()
