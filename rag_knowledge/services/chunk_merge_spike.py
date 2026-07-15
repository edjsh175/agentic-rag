"""Offline short-section merge simulator.

Round 0C production logic lives in ``section_chunk_merge``. This module
re-exports the same API so existing spike scripts/tests keep working.
Loader must import ``section_chunk_merge``, not this module.
"""

from __future__ import annotations

from rag_knowledge.services.section_chunk_merge import (  # noqa: F401
    CHUNKING_METHOD,
    TARGET_MIN,
    TARGET_SOFT_MAX,
    MergeUnit,
    apply_technical_manual_merge,
    chunk_uid_for,
    document_key_from_meta,
    documents_to_merge_units,
    fact_window_coverage,
    length_stats,
    merge_units_to_documents,
    reassign_chunk_adjacency,
    section_id_for,
)

# Back-compat alias used by older notes/tests.
section_id_for_path = section_id_for

__all__ = [
    "CHUNKING_METHOD",
    "TARGET_MIN",
    "TARGET_SOFT_MAX",
    "MergeUnit",
    "apply_technical_manual_merge",
    "chunk_uid_for",
    "document_key_from_meta",
    "documents_to_merge_units",
    "fact_window_coverage",
    "length_stats",
    "merge_units_to_documents",
    "reassign_chunk_adjacency",
    "section_id_for",
    "section_id_for_path",
]
