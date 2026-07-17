import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyError
from rag_knowledge.services.rebuild_coordinator import RebuildAlreadyRunningError, RebuildCoordinator
from rag_knowledge.repository.vector_store import VectorStore

FIXED_NOW = datetime(2026, 7, 10, 12, 0, 0, 123456)
OPERATION_ID = FIXED_NOW.strftime("%Y%m%d-%H%M%S-%f")


@pytest.fixture
def fixed_operation_time(monkeypatch):
    class FixedDateTime:
        @classmethod
        def now(cls):
            return FIXED_NOW

    monkeypatch.setattr("rag_knowledge.services.rebuild_coordinator.datetime", FixedDateTime)


class TrackingStore:
    def __init__(self, collection_name: str = "rag_knowledge"):
        self._collection_name = collection_name
        self._store = object()
        self.events: list[tuple] = []
        self._chunk_snapshot = {"ids": ["c1"], "documents": ["doc"], "metadatas": [{}]}

    def fork(self, collection_name: str) -> "TrackingStore":
        self.events.append(("fork", collection_name))
        child = TrackingStore(collection_name)
        child.events = self.events
        return child

    def rename_collection(self, new_name: str) -> None:
        self.events.append(("rename_collection", self._collection_name, new_name))
        self._collection_name = new_name

    def disconnect(self) -> None:
        self.events.append(("disconnect",))

    def clear(self) -> None:
        self.events.append(("clear",))

    def get_chunk_stats_source(self) -> dict:
        return dict(self._chunk_snapshot)


def _make_coordinator(
    tmp_path,
    *,
    live_store,
    live_scanner=None,
    staged_scanner=None,
    consistency_service=None,
    invalidate=None,
    rebuild_bm25=None,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    live_index = data_dir / "file_index.json"
    if not live_index.exists():
        live_index.write_text('{"version":1,"files":{"h1":{"chunk_ids":["c1"]}}}', encoding="utf-8")

    staged_scanner = staged_scanner or MagicMock()
    staged_scanner.reset_index = MagicMock()
    staged_scanner.scan.return_value = {"new_files": 1, "skipped_files": 0, "errors": 0}

    coordinator = RebuildCoordinator(
        cfg=SimpleNamespace(
            data_dir=data_dir,
            collection_name="rag_knowledge",
            relational_db_path=data_dir / "rag_relational.db",
        ),
        store=live_store,
        scanner=live_scanner or MagicMock(),
        consistency_service=consistency_service or SimpleNamespace(assert_consistent=MagicMock()),
        invalidate_retrieval_caches=invalidate or MagicMock(),
        rebuild_bm25=rebuild_bm25 or MagicMock(),
        staging_scanner_factory=lambda _store, _index: staged_scanner,
    )
    return coordinator, data_dir, live_index, staged_scanner


def test_vector_store_fork_uses_independent_collection_handle(isolated_storage):
    isolated_storage()
    live = VectorStore()
    staged = live.fork("rag_knowledge__staging__op1")
    assert staged is not live
    assert staged._persist_dir == live._persist_dir
    assert staged._embeddings is live._embeddings
    assert staged._collection_name == "rag_knowledge__staging__op1"
    assert live._collection_name != staged._collection_name


def test_staging_scan_errors_leave_live_unchanged(tmp_path, fixed_operation_time):
    live_store = TrackingStore()
    live_content = '{"version":1,"files":{"h1":{"chunk_ids":["c1"]}}}'
    coordinator, data_dir, live_index, staged_scanner = _make_coordinator(tmp_path, live_store=live_store)
    live_index.write_text(live_content, encoding="utf-8")
    staged_scanner.scan.return_value = {"new_files": 0, "skipped_files": 0, "errors": 2}

    with pytest.raises(RuntimeError, match="staging rebuild has 2 file errors"):
        coordinator.run()

    assert live_index.read_text(encoding="utf-8") == live_content
    assert ("clear",) not in live_store.events
    assert not any(event[0] == "rename_collection" for event in live_store.events)
    state = json.loads((data_dir / "rebuild_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed_before_commit"
    assert state["stage"] == "staging"


def test_staging_consistency_failure_cleans_staging_and_preserves_live(tmp_path, fixed_operation_time, monkeypatch):
    live_store = TrackingStore()
    live_content = '{"version":1,"files":{"h1":{"chunk_ids":["c1"]}}}'
    coordinator, data_dir, live_index, staged_scanner = _make_coordinator(tmp_path, live_store=live_store)
    live_index.write_text(live_content, encoding="utf-8")

    def _write_staging_index():
        staging_index = data_dir / "rebuild" / OPERATION_ID / "file_index.json"
        staging_index.parent.mkdir(parents=True, exist_ok=True)
        staging_index.write_text('{"version":1,"files":{"h1":{"chunk_ids":["c1"]}}}', encoding="utf-8")
        return {"new_files": 1, "skipped_files": 0, "errors": 0}

    staged_scanner.scan.side_effect = _write_staging_index

    class FakeConsistencyService:
        def __init__(self, *, cfg=None, index_data=None, chunk_snapshot=None):
            self._staging = index_data is not None

        def assert_consistent(self, *, source=None):
            if self._staging:
                raise KnowledgeBaseConsistencyError({"summary": {"consistent": False}})
            return {"summary": {"consistent": True}}

    monkeypatch.setattr(
        "rag_knowledge.services.rebuild_coordinator.KnowledgeBaseConsistencyService",
        FakeConsistencyService,
    )

    with pytest.raises(KnowledgeBaseConsistencyError):
        coordinator.run()

    assert live_index.read_text(encoding="utf-8") == live_content
    assert ("clear",) in live_store.events
    assert not any(event[0] == "rename_collection" for event in live_store.events)


def test_commit_swap_rolls_back_when_index_replace_fails(tmp_path, fixed_operation_time, monkeypatch):
    live_store = TrackingStore()
    coordinator, data_dir, live_index, staged_scanner = _make_coordinator(tmp_path, live_store=live_store)
    live_content = '{"version":1,"files":{"live":{"chunk_ids":["c1"]}}}'
    live_index.write_text(live_content, encoding="utf-8")

    staging_index = data_dir / "rebuild" / OPERATION_ID / "file_index.json"

    def _write_staging_index():
        staging_index.parent.mkdir(parents=True, exist_ok=True)
        staging_index.write_text('{"version":1,"files":{"staging":{"chunk_ids":["c2"]}}}', encoding="utf-8")
        return {"new_files": 1, "skipped_files": 0, "errors": 0}

    staged_scanner.scan.side_effect = _write_staging_index

    class AlwaysConsistentService:
        def __init__(self, *, cfg=None, index_data=None, chunk_snapshot=None):
            pass

        def assert_consistent(self, *, source=None):
            return {"summary": {"consistent": True}}

    monkeypatch.setattr(
        "rag_knowledge.services.rebuild_coordinator.KnowledgeBaseConsistencyService",
        AlwaysConsistentService,
    )
    monkeypatch.setattr(
        "rag_knowledge.services.rebuild_coordinator.os.replace",
        MagicMock(side_effect=OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        coordinator.run()

    assert live_index.read_text(encoding="utf-8") == live_content
    assert live_store._collection_name == "rag_knowledge"
    rename_events = [event for event in live_store.events if event[0] == "rename_collection"]
    assert ("rename_collection", "rag_knowledge", f"rag_knowledge__backup__{OPERATION_ID}") in rename_events
    assert ("rename_collection", f"rag_knowledge__backup__{OPERATION_ID}", "rag_knowledge") in rename_events
    state = json.loads((data_dir / "rebuild_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "rolled_back"
    assert state["stage"] == "committing"


def test_successful_rebuild_swaps_staging_and_refreshes_live_only_after_commit(
    tmp_path, fixed_operation_time, monkeypatch
):
    live_store = TrackingStore()
    live_scanner = MagicMock()
    invalidate = MagicMock()
    rebuild_bm25 = MagicMock()
    live_checker = SimpleNamespace(assert_consistent=MagicMock())
    coordinator, data_dir, live_index, staged_scanner = _make_coordinator(
        tmp_path,
        live_store=live_store,
        live_scanner=live_scanner,
        consistency_service=live_checker,
        invalidate=invalidate,
        rebuild_bm25=rebuild_bm25,
    )
    live_index.write_text('{"version":1,"files":{"live":{"chunk_ids":["c1"]}}}', encoding="utf-8")
    staging_index = data_dir / "rebuild" / OPERATION_ID / "file_index.json"

    def _write_staging_index():
        staging_index.parent.mkdir(parents=True, exist_ok=True)
        staging_index.write_text('{"version":1,"files":{"staging":{"chunk_ids":["c2"]}}}', encoding="utf-8")
        return {"new_files": 3, "skipped_files": 1, "errors": 0}

    staged_scanner.scan.side_effect = _write_staging_index

    class AlwaysConsistentService:
        def __init__(self, *, cfg=None, index_data=None, chunk_snapshot=None):
            pass

        def assert_consistent(self, *, source=None):
            return {"summary": {"consistent": True}}

    monkeypatch.setattr(
        "rag_knowledge.services.rebuild_coordinator.KnowledgeBaseConsistencyService",
        AlwaysConsistentService,
    )

    result = coordinator.run()

    assert result == {
        "message": "知识库已重建",
        "operation_id": OPERATION_ID,
        "backup_collection": f"rag_knowledge__backup__{OPERATION_ID}",
        "index_backup_path": str(data_dir / "rebuild" / OPERATION_ID / "file_index.before.json"),
        "graph_backup_path": "",
        "new_files": 3,
        "skipped_files": 1,
        "errors": 0,
    }
    assert json.loads(live_index.read_text(encoding="utf-8")) == {
        "version": 1,
        "files": {"staging": {"chunk_ids": ["c2"]}},
    }
    assert live_store.events == [
        ("fork", f"rag_knowledge__staging__{OPERATION_ID}"),
        ("rename_collection", "rag_knowledge", f"rag_knowledge__backup__{OPERATION_ID}"),
        ("rename_collection", f"rag_knowledge__staging__{OPERATION_ID}", "rag_knowledge"),
        ("disconnect",),
    ]
    live_scanner.reload_index.assert_called_once_with()
    live_checker.assert_consistent.assert_not_called()
    rebuild_bm25.assert_called_once_with()
    invalidate.assert_called_once_with("rebuild_commit")
    assert not (data_dir / "rebuild_state.json").exists()


def test_post_commit_validation_failure_does_not_clear_committed_collection(
    tmp_path, fixed_operation_time, monkeypatch
):
    live_store = TrackingStore()
    coordinator, data_dir, _live_index, staged_scanner = _make_coordinator(
        tmp_path,
        live_store=live_store,
    )
    staging_index = data_dir / "rebuild" / OPERATION_ID / "file_index.json"

    def _write_staging_index():
        staging_index.parent.mkdir(parents=True, exist_ok=True)
        staging_index.write_text(
            '{"version":1,"files":{"staging":{"chunk_ids":["c2"]}}}',
            encoding="utf-8",
        )
        return {"new_files": 1, "skipped_files": 0, "errors": 0}

    staged_scanner.scan.side_effect = _write_staging_index

    class FailsAfterCommitService:
        calls = 0

        def __init__(self, *, cfg=None, index_data=None, chunk_snapshot=None):
            pass

        def assert_consistent(self, *, source=None):
            type(self).calls += 1
            if type(self).calls == 2:
                raise KnowledgeBaseConsistencyError({"summary": {"consistent": False}})
            return {"summary": {"consistent": True}}

    monkeypatch.setattr(
        "rag_knowledge.services.rebuild_coordinator.KnowledgeBaseConsistencyService",
        FailsAfterCommitService,
    )

    with pytest.raises(KnowledgeBaseConsistencyError):
        coordinator.run()

    assert ("clear",) not in live_store.events
    state = json.loads((data_dir / "rebuild_state.json").read_text(encoding="utf-8"))
    assert state["stage"] == "post_commit_validation"
    assert state["status"] == "failed_after_commit"


def test_rebuild_coordinator_rejects_concurrent_rebuild(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lock_path = data_dir / "rebuild.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid(), "started_at": "20260708-000000"}), encoding="utf-8")

    coordinator = RebuildCoordinator(
        cfg=SimpleNamespace(
            data_dir=data_dir,
            collection_name="rag_knowledge",
            relational_db_path=data_dir / "rag_relational.db",
        ),
        store=MagicMock(),
        scanner=MagicMock(),
        consistency_service=SimpleNamespace(assert_consistent=MagicMock()),
        invalidate_retrieval_caches=MagicMock(),
        rebuild_bm25=MagicMock(),
    )

    with pytest.raises(RebuildAlreadyRunningError):
        coordinator.run()


def test_rebuild_coordinator_removes_lock_after_failure(tmp_path, fixed_operation_time):
    live_store = TrackingStore()
    coordinator, data_dir, _live_index, staged_scanner = _make_coordinator(tmp_path, live_store=live_store)
    staged_scanner.scan.side_effect = RuntimeError("scan failed")

    with pytest.raises(RuntimeError, match="scan failed"):
        coordinator.run()

    assert not (data_dir / "rebuild.lock").exists()


def test_rebuild_coordinator_decisions_transaction(tmp_path, fixed_operation_time, monkeypatch):
    live_store = TrackingStore()
    coordinator, data_dir, _live_index, staged_scanner = _make_coordinator(tmp_path, live_store=live_store)

    live_decision = data_dir / "ingestion_decisions.json"
    live_decision.write_text('{"version": 1, "decisions": {"d1": {"status": "queued"}}}', encoding="utf-8")

    def mock_scan():
        staging_decision = data_dir / "rebuild" / OPERATION_ID / "ingestion_decisions.json"
        staging_decision.parent.mkdir(parents=True, exist_ok=True)
        staging_decision.write_text('{"version": 1, "decisions": {"d2": {"status": "excluded"}}}', encoding="utf-8")
        return {"new_files": 1, "skipped_files": 0, "errors": 0}

    staged_scanner.scan.side_effect = mock_scan

    class AlwaysConsistentService:
        def __init__(self, *, cfg=None, index_data=None, chunk_snapshot=None):
            pass

        def assert_consistent(self, *, source=None):
            return {"summary": {"consistent": True}}

    monkeypatch.setattr(
        "rag_knowledge.services.rebuild_coordinator.KnowledgeBaseConsistencyService",
        AlwaysConsistentService,
    )

    coordinator.run()

    assert live_decision.exists()
    assert json.loads(live_decision.read_text(encoding="utf-8")) == {"version": 1, "decisions": {"d2": {"status": "excluded"}}}

    # Test rollback
    live_decision.write_text('{"version": 1, "decisions": {"d1": {"status": "queued"}}}', encoding="utf-8")

    # Mock fork to return a TrackingStore child that raises exception on rename_collection
    def failing_fork(collection_name):
        child = TrackingStore(collection_name)
        child.events = live_store.events
        child.rename_collection = MagicMock(side_effect=Exception("swap fail"))
        return child

    live_store.fork = failing_fork

    staged_scanner.scan.side_effect = mock_scan

    with pytest.raises(Exception, match="swap fail"):
        coordinator.run()

    assert live_decision.exists()
    assert json.loads(live_decision.read_text(encoding="utf-8")) == {"version": 1, "decisions": {"d1": {"status": "queued"}}}
