"""Unit test for retrieval soft penalization of down-voted chunks."""
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy


def test_retrieval_soft_penalization(isolated_storage):
    isolated_storage()
    db = RelationalDB()

    # Create down feedback for chunk_b
    db.create_feedback(
        user_id="u1",
        query_text="测试查询",
        answer_text="测试回答",
        referenced_chunk_ids=["chunk_b"],
        rating="down",
        reason="低质量片段",
        trace_id="t_penalize_1",
        feedback_scope="chunk",
        target_chunk_id="chunk_b",
    )

    doc_a = Document(page_content="内容 A", metadata={"chunk_id": "chunk_a", "score": 0.85})
    doc_b = Document(page_content="内容 B", metadata={"chunk_id": "chunk_b", "score": 0.88})

    strategy = RetrievalQualityStrategy(Config())
    results = strategy.apply(query="测试查询", docs=[doc_a, doc_b])

    # doc_b initially had higher score (0.88), but chunk_b was penalized (0.88 * 0.85 = ~0.748)
    # doc_a remains at 0.85 and should rank first
    assert len(results) == 2
    assert results[0].metadata["chunk_id"] == "chunk_a"
    assert results[1].metadata["chunk_id"] == "chunk_b"

    b_meta = results[1].metadata
    assert "down_penalty_factor" in b_meta
    assert abs(b_meta["down_penalty_factor"] - 0.85) < 0.05
    assert abs(b_meta["quality_score"] - 0.748) < 0.01
