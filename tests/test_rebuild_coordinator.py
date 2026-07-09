import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyError
from rag_knowledge.services.rebuild_coordinator import RebuildAlreadyRunningError, RebuildCoordinator


def test_rebuild_coordinator_backs_up_index_and_clears_state_on_success(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    index_path = data_dir / "file_index.json"
    index_path.write_text('{"files": {"hash-1": {"chunk_ids": ["c1"]}}}', encoding="utf-8")

    scanner = MagicMock()
    scanner.scan.return_value = {"new_files": 1, "skipped_files": 0, "errors": 0}
    store = MagicMock()
    checker = SimpleNamespace(assert_consistent=MagicMock())
    invalidate = MagicMock()
    rebuild_bm25 = MagicMock()

    coordinator = RebuildCoordinator(
        cfg=SimpleNamespace(data_dir=data_dir),
        store=store,
        scanner=scanner,
        consistency_service=checker,
        invalidate_retrieval_caches=invalidate,
        rebuild_bm25=rebuild_bm25,
    )

    result = coordinator.run()

    assert result["message"] == "知识库已重建"
    assert result["backup_path"]
    assert Path(result["backup_path"]).exists()
    assert not (data_dir / "rebuild_state.json").exists()
    store.clear.assert_called_once_with()
    scanner.reset_index.assert_called_once_with()
    scanner.scan.assert_called_once_with()
    checker.assert_consistent.assert_called_once_with()
    rebuild_bm25.assert_called_once_with()


def test_rebuild_coordinator_persists_failed_state_when_consistency_check_fails(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file_index.json").write_text('{"files": {}}', encoding="utf-8")

    scanner = MagicMock()
    scanner.scan.return_value = {"new_files": 1, "skipped_files": 0, "errors": 0}
    store = MagicMock()
    error = KnowledgeBaseConsistencyError(
        {
            "summary": {
                "consistent": False,
                "missing_indexed_chunk_total": 3,
            }
        }
    )
    checker = SimpleNamespace(assert_consistent=MagicMock(side_effect=error))

    coordinator = RebuildCoordinator(
        cfg=SimpleNamespace(data_dir=data_dir),
        store=store,
        scanner=scanner,
        consistency_service=checker,
        invalidate_retrieval_caches=MagicMock(),
        rebuild_bm25=MagicMock(),
    )

    with pytest.raises(KnowledgeBaseConsistencyError):
        coordinator.run()

    state_path = data_dir / "rebuild_state.json"
    assert state_path.exists()
    assert '"status": "failed"' in state_path.read_text(encoding="utf-8")


def test_rebuild_coordinator_rejects_concurrent_rebuild(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lock_path = data_dir / "rebuild.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid(), "started_at": "20260708-000000"}), encoding="utf-8")

    coordinator = RebuildCoordinator(
        cfg=SimpleNamespace(data_dir=data_dir),
        store=MagicMock(),
        scanner=MagicMock(),
        consistency_service=SimpleNamespace(assert_consistent=MagicMock()),
        invalidate_retrieval_caches=MagicMock(),
        rebuild_bm25=MagicMock(),
    )

    with pytest.raises(RebuildAlreadyRunningError):
        coordinator.run()


def test_rebuild_coordinator_removes_lock_after_failure(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scanner = MagicMock()
    scanner.scan.side_effect = RuntimeError("scan failed")

    coordinator = RebuildCoordinator(
        cfg=SimpleNamespace(data_dir=data_dir),
        store=MagicMock(),
        scanner=scanner,
        consistency_service=SimpleNamespace(assert_consistent=MagicMock()),
        invalidate_retrieval_caches=MagicMock(),
        rebuild_bm25=MagicMock(),
    )

    with pytest.raises(RuntimeError):
        coordinator.run()

    assert not (data_dir / "rebuild.lock").exists()
