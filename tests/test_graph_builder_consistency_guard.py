import pytest

from rag_knowledge.services.graph_extraction import GraphBuilder, pipeline
from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyError


def test_graph_builder_blocks_live_build_when_knowledge_base_is_inconsistent(isolated_storage, monkeypatch):
    isolated_storage(db_name="graph.db")

    class FakeConsistencyService:
        def assert_consistent(self):
            raise KnowledgeBaseConsistencyError(
                {
                    "summary": {
                        "consistent": False,
                        "missing_indexed_chunk_total": 5,
                    }
                }
            )

    monkeypatch.setattr(pipeline, "KnowledgeBaseConsistencyService", lambda: FakeConsistencyService())

    with pytest.raises(KnowledgeBaseConsistencyError):
        GraphBuilder().build_full()
