import asyncio
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import types
import pytest

_INJECTED_MODULES = []


def _inject_stub(name, stub):
    if name not in sys.modules:
        sys.modules[name] = stub
        _INJECTED_MODULES.append(name)


unstructured_module = types.ModuleType("unstructured")
unstructured_chunking_module = types.ModuleType("unstructured.chunking")
unstructured_chunking_title_module = types.ModuleType("unstructured.chunking.title")
unstructured_partition_module = types.ModuleType("unstructured.partition")
unstructured_partition_docx_module = types.ModuleType("unstructured.partition.docx")
unstructured_partition_md_module = types.ModuleType("unstructured.partition.md")
unstructured_partition_text_module = types.ModuleType("unstructured.partition.text")
unstructured_chunking_title_module.chunk_by_title = lambda *args, **kwargs: []
unstructured_partition_docx_module.partition_docx = lambda *args, **kwargs: []
unstructured_partition_md_module.partition_md = lambda *args, **kwargs: []
unstructured_partition_text_module.partition_text = lambda *args, **kwargs: []

_inject_stub("unstructured", unstructured_module)
_inject_stub("unstructured.chunking", unstructured_chunking_module)
_inject_stub("unstructured.chunking.title", unstructured_chunking_title_module)
_inject_stub("unstructured.partition", unstructured_partition_module)
_inject_stub("unstructured.partition.docx", unstructured_partition_docx_module)
_inject_stub("unstructured.partition.md", unstructured_partition_md_module)
_inject_stub("unstructured.partition.text", unstructured_partition_text_module)


def tearDownModule():
    for name in _INJECTED_MODULES:
        sys.modules.pop(name, None)

from rag_knowledge.api import routes
from rag_knowledge.models.api import QueryRequest
from rag_knowledge.services.rag import NO_KNOWLEDGE_ANSWER, RagChain


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    isolated_storage(
        db_name="chunk-stats.db",
        chroma_name="chunk-stats-chroma",
        data_dir_name="chunk-stats-data",
    )


class _StoreStub:
    def __init__(self, payload):
        self._payload = payload

    def get_chunk_stats_source(self):
        return self._payload


class ChunkStatsServiceTests(unittest.TestCase):
    def test_builds_chunk_stats_response_from_store_index_and_summaries(self):
        from rag_knowledge.services.chunk_hit_telemetry import ChunkHitTelemetry
        from rag_knowledge.services.chunk_stats import ChunkStatsService

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_index_path = root / "file_index.json"
            telemetry_path = root / "chunk_hit_stats.json"
            offline_path = root / "eval_summary.json"

            file_index_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "files": {
                            "hash-a": {
                                "file_path": "upload/a.md",
                                "file_name": "a.md",
                                "kb_name": "文章附件",
                                "doc_category": "其他",
                                "category": "text",
                                "chunk_ids": ["chunk-1", "chunk-2"],
                            },
                            "hash-b": {
                                "file_path": "upload/b.pdf",
                                "file_name": "b.pdf",
                                "kb_name": "文章附件",
                                "doc_category": "运维管理",
                                "category": "text",
                                "chunk_ids": ["chunk-3"],
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            offline_path.write_text(
                json.dumps(
                    {
                        "evaluated_at": "2026-07-03T16:00:00",
                        "sample_count": 12,
                        "hit_rate": 0.75,
                        "recall_at_k": {"5": 0.83},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            telemetry = ChunkHitTelemetry(telemetry_path)
            telemetry.record_query(
                [
                    {"metadata": {"chunk_id": "chunk-1"}},
                    {"metadata": {"chunk_id": "chunk-2"}},
                    {"metadata": {"chunk_id": "chunk-1"}},
                ]
            )
            telemetry.record_query([])

            service = ChunkStatsService(
                cfg=SimpleNamespace(context_budget=SimpleNamespace(chars_per_token=2.0)),
                store=_StoreStub(
                    {
                        "ids": ["chunk-1", "chunk-2", "chunk-3"],
                        "documents": ["abcd", "abcdefgh", "abc"],
                        "metadatas": [
                            {"chunk_id": "chunk-1", "review_status": "approved", "source": "a.md"},
                            {"chunk_id": "chunk-2", "review_status": "pending", "source": "a.md"},
                            {"chunk_id": "chunk-3", "review_status": "approved", "source": "b.pdf"},
                        ],
                    }
                ),
                telemetry=telemetry,
                file_index_path=file_index_path,
                offline_summary_path=offline_path,
            )

            result = service.build().model_dump()

            self.assertEqual(result["overview"]["total_chunks"], 3)
            self.assertAlmostEqual(result["overview"]["avg_chunk_length"], 5.0)
            self.assertAlmostEqual(result["overview"]["avg_chunk_tokens"], 2.5)
            self.assertEqual(result["overview"]["min_chunk_length"], 3)
            self.assertEqual(result["overview"]["max_chunk_length"], 8)

            self.assertEqual(
                result["distributions"]["by_file"][0],
                {
                    "file_path": "upload/a.md",
                    "file_name": "a.md",
                    "kb_name": "文章附件",
                    "doc_category": "其他",
                    "file_type": "text",
                    "chunk_count": 2,
                },
            )
            self.assertEqual(
                result["distributions"]["by_review_status"],
                [
                    {"key": "approved", "chunk_count": 2},
                    {"key": "pending", "chunk_count": 1},
                ],
            )
            self.assertEqual(
                result["hit_rates"]["online"]["query_hit_rate"],
                0.5,
            )
            self.assertEqual(
                result["hit_rates"]["online"]["top_chunks"][0]["chunk_id"],
                "chunk-1",
            )
            self.assertEqual(
                result["hit_rates"]["online"]["by_review_status"],
                [
                    {"key": "approved", "hit_count": 1},
                    {"key": "pending", "hit_count": 1},
                ],
            )
            self.assertTrue(result["hit_rates"]["offline"]["available"])
            self.assertEqual(result["hit_rates"]["offline"]["sample_count"], 12)
            self.assertEqual(result["hit_rates"]["offline"]["recall_at_k"], {"5": 0.83})

    def test_handles_empty_chunks_missing_review_status_and_missing_offline_summary(self):
        from rag_knowledge.services.chunk_hit_telemetry import ChunkHitTelemetry
        from rag_knowledge.services.chunk_stats import ChunkStatsService

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_index_path = root / "file_index.json"
            file_index_path.write_text(json.dumps({"version": 1, "files": {}}, ensure_ascii=False), encoding="utf-8")

            telemetry = ChunkHitTelemetry(root / "chunk_hit_stats.json")
            service = ChunkStatsService(
                cfg=SimpleNamespace(context_budget=SimpleNamespace(chars_per_token=1.3)),
                store=_StoreStub(
                    {
                        "ids": ["chunk-1"],
                        "documents": [""],
                        "metadatas": [{"chunk_id": "chunk-1", "source": "missing.md"}],
                    }
                ),
                telemetry=telemetry,
                file_index_path=file_index_path,
                offline_summary_path=root / "missing_summary.json",
            )

            result = service.build().model_dump()

            self.assertEqual(result["overview"]["total_chunks"], 1)
            self.assertEqual(result["overview"]["avg_chunk_length"], 0.0)
            self.assertEqual(
                result["distributions"]["by_review_status"],
                [{"key": "unknown", "chunk_count": 1}],
            )
            self.assertEqual(result["distributions"]["by_file"], [])
            self.assertFalse(result["hit_rates"]["offline"]["available"])
            self.assertEqual(result["hit_rates"]["online"]["total_queries"], 0)


class ChunkHitTelemetryTests(unittest.TestCase):
    def test_records_unique_chunk_hits_and_misses(self):
        from rag_knowledge.services.chunk_hit_telemetry import ChunkHitTelemetry

        with tempfile.TemporaryDirectory() as tmp:
            telemetry = ChunkHitTelemetry(Path(tmp) / "chunk_hit_stats.json")
            telemetry.record_query(
                [
                    {"metadata": {"chunk_id": "chunk-1"}},
                    {"metadata": {"chunk_id": "chunk-1"}},
                    {"metadata": {"chunk_id": "chunk-2"}},
                    {"metadata": {"source_type": "external"}},
                ]
            )
            telemetry.record_query([])

            payload = telemetry.read()

            self.assertEqual(payload["total_queries"], 2)
            self.assertEqual(payload["hit_queries"], 1)
            self.assertEqual(payload["chunk_hits"], {"chunk-1": 1, "chunk-2": 1})
            self.assertIsNotNone(payload["last_updated_at"])


class ChunkStatsRouteTests(unittest.TestCase):
    def test_stats_route_shape_is_unchanged(self):
        original_cfg = routes._cfg

        with patch("rag_knowledge.api.routes.VectorStore") as store_cls:
            store_cls.return_value.count.return_value = 7
            routes._cfg = SimpleNamespace(
                collection_name="rag_knowledge",
                watch_dir=Path("watch_directory"),
                watch_file_types=["pdf", "md"],
                scan_interval=30,
            )
            response = routes.stats()

        routes._cfg = original_cfg

        self.assertEqual(
            set(response.model_dump().keys()),
            {
                "total_chunks",
                "collection_name",
                "watched_directory",
                "file_types",
                "scan_interval_minutes",
            },
        )

    def test_stats_chunks_route_returns_empty_shape(self):
        original_cfg = routes._cfg

        class _ServiceStub:
            def build(self):
                from rag_knowledge.models.api import ChunkStatsDistributions, ChunkStatsHitRates
                from rag_knowledge.models.api import (
                    ChunkStatsOfflineHitRates,
                    ChunkStatsOnlineHitRates,
                    ChunkStatsOverview,
                    ChunkStatsResponse,
                )

                return ChunkStatsResponse(
                    overview=ChunkStatsOverview(
                        total_chunks=0,
                        avg_chunk_tokens=0.0,
                        avg_chunk_length=0.0,
                        min_chunk_length=0,
                        max_chunk_length=0,
                    ),
                    distributions=ChunkStatsDistributions(
                        by_file=[],
                        by_file_type=[],
                        by_review_status=[],
                    ),
                    hit_rates=ChunkStatsHitRates(
                        online=ChunkStatsOnlineHitRates(
                            total_queries=0,
                            hit_queries=0,
                            query_hit_rate=0.0,
                            top_chunks=[],
                            by_review_status=[],
                            by_file_type=[],
                            last_updated_at=None,
                        ),
                        offline=ChunkStatsOfflineHitRates(
                            available=False,
                            evaluated_at=None,
                            sample_count=0,
                            hit_rate=0.0,
                            recall_at_k={},
                        ),
                    ),
                )

        routes._cfg = SimpleNamespace()
        with patch("rag_knowledge.api.routes.ChunkStatsService", return_value=_ServiceStub()):
            response = routes.chunk_stats()
        routes._cfg = original_cfg

        self.assertEqual(response.overview.total_chunks, 0)
        self.assertFalse(response.hit_rates.offline.available)


class RagChunkHitTelemetryTests(unittest.TestCase):
    def test_query_records_chunk_hits_after_retrieval(self):
        chain = object.__new__(RagChain)
        chain._llm_model = "test-model"
        chain._allow_general_knowledge = False
        chain._record_chunk_hit_query = MagicMock()
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._retrieve_multi = MagicMock(return_value=([], ""))

        result = chain.query("question", allow_general_knowledge=False)

        self.assertEqual(result, {"answer": NO_KNOWLEDGE_ANSWER, "source_documents": []})
        chain._record_chunk_hit_query.assert_called_once_with([])

    def test_query_does_not_record_hits_for_greeting_or_failure(self):
        chain = object.__new__(RagChain)
        chain._llm_model = "test-model"
        chain._record_chunk_hit_query = MagicMock()

        result = chain.query("你好")
        self.assertEqual(result["source_documents"], [])
        chain._record_chunk_hit_query.assert_not_called()

        chain = object.__new__(RagChain)
        chain._llm_model = "test-model"
        chain._allow_general_knowledge = False
        chain._record_chunk_hit_query = MagicMock()
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._retrieve_multi = MagicMock(side_effect=RuntimeError("boom"))

        result = chain.query("question", allow_general_knowledge=False)

        self.assertIn("查询出错", result["answer"])
        chain._record_chunk_hit_query.assert_not_called()

    def test_stream_query_records_chunk_hits_after_retrieval(self):
        chain = object.__new__(RagChain)
        chain._llm_model = "test-model"
        chain._allow_general_knowledge = False
        chain._query_cache = MagicMock()
        chain._aretrieve_uncached = AsyncMock(return_value=([], ""))
        chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
        chain._record_chunk_hit_query = MagicMock()
        chain._build_retrieval_query_specs = lambda question, history: ["question"]

        async def collect():
            return [
                event
                async for event in chain.stream_query(
                    "question", allow_general_knowledge=False
                )
            ]

        events = asyncio.run(collect())

        self.assertIn({"type": "sources", "data": []}, events)
        chain._record_chunk_hit_query.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
