from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from rag_knowledge.services.knowledge_base_consistency import (
    KnowledgeBaseConsistencyError,
    KnowledgeBaseConsistencyService,
)


class RebuildAlreadyRunningError(RuntimeError):
    pass


def _backup_sqlite(source: Path, destination: Path) -> str:
    if not source.exists():
        return ""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    return str(destination)


def _commit_swap(
    *,
    live_store,
    staged_store,
    live_name: str,
    staging_name: str,
    backup_name: str,
    live_index: Path,
    staging_index: Path,
    index_backup: Path,
) -> None:
    shutil.copy2(live_index, index_backup)
    live_store.rename_collection(backup_name)
    try:
        staged_store.rename_collection(live_name)
        os.replace(staging_index, live_index)
    except Exception:
        staged_store.rename_collection(staging_name)
        live_store.rename_collection(live_name)
        shutil.copy2(index_backup, live_index)
        raise


class RebuildCoordinator:
    def __init__(
        self,
        *,
        cfg,
        store,
        scanner,
        consistency_service,
        invalidate_retrieval_caches: Callable[[str], None],
        rebuild_bm25: Callable[[], None],
        staging_scanner_factory: Callable | None = None,
    ):
        self._cfg = cfg
        self._store = store
        self._scanner = scanner
        self._consistency_service = consistency_service
        self._invalidate_retrieval_caches = invalidate_retrieval_caches
        self._rebuild_bm25 = rebuild_bm25
        self._staging_scanner_factory = staging_scanner_factory

    def run(self) -> dict:
        data_dir = Path(self._cfg.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        state_path = data_dir / "rebuild_state.json"
        lock_path = data_dir / "rebuild.lock"
        live_index = data_dir / "file_index.json"

        operation_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        live_name = self._cfg.collection_name
        staging_name = f"{live_name}__staging__{operation_id}"
        backup_name = f"{live_name}__backup__{operation_id}"
        work_dir = data_dir / "rebuild" / operation_id
        staging_index = work_dir / "file_index.json"
        index_backup = work_dir / "file_index.before.json"
        graph_backup = work_dir / "rag_relational.before.db"
        staging_scanner_factory = self._staging_scanner_factory or self._default_staging_scanner_factory

        staged_store = None
        with self._exclusive_lock(lock_path, operation_id):
            graph_backup_path = _backup_sqlite(Path(self._cfg.relational_db_path), graph_backup)
            work_dir.mkdir(parents=True, exist_ok=True)
            if live_index.exists():
                shutil.copy2(live_index, staging_index)
            else:
                staging_index.write_text(
                    json.dumps({"version": 1, "files": {}}, ensure_ascii=False),
                    encoding="utf-8",
                )

            base_state = {
                "operation_id": operation_id,
                "staging_collection": staging_name,
                "backup_collection": backup_name,
                "index_backup_path": str(index_backup),
                "graph_backup_path": graph_backup_path,
            }

            try:
                self._write_state(state_path, {**base_state, "stage": "staging", "status": "running"})
                staged_store = self._store.fork(staging_name)
                staged_scanner = staging_scanner_factory(staged_store, staging_index)
                staged_scanner.reset_index()
                result = staged_scanner.scan()
                if result["errors"]:
                    raise RuntimeError(f"staging rebuild has {result['errors']} file errors")

                self._write_state(state_path, {**base_state, "stage": "validating", "status": "running"})
                index_data = json.loads(staging_index.read_text(encoding="utf-8"))
                chunk_snapshot = staged_store.get_chunk_stats_source()
                KnowledgeBaseConsistencyService(
                    index_data=index_data,
                    chunk_snapshot=chunk_snapshot,
                ).assert_consistent()

                self._write_state(state_path, {**base_state, "stage": "committing", "status": "running"})
                _commit_swap(
                    live_store=self._store,
                    staged_store=staged_store,
                    live_name=live_name,
                    staging_name=staging_name,
                    backup_name=backup_name,
                    live_index=live_index,
                    staging_index=staging_index,
                    index_backup=index_backup,
                )

                self._store.disconnect()
                self._scanner.reload_index()
                self._consistency_service.assert_consistent()
                self._rebuild_bm25()
                self._invalidate_retrieval_caches("rebuild_commit")
                if state_path.exists():
                    state_path.unlink()
                return {
                    "message": "知识库已重建",
                    "operation_id": operation_id,
                    "backup_collection": backup_name,
                    "index_backup_path": str(index_backup),
                    "graph_backup_path": graph_backup_path,
                    "new_files": result["new_files"],
                    "skipped_files": result["skipped_files"],
                    "errors": result["errors"],
                }
            except KnowledgeBaseConsistencyError as exc:
                self._cleanup_staging_store(staged_store)
                self._write_state(
                    state_path,
                    {
                        **base_state,
                        "stage": "validating",
                        "status": "failed_before_commit",
                        "error": str(exc),
                        "report": exc.report,
                    },
                )
                raise
            except RuntimeError as exc:
                if str(exc).startswith("staging rebuild has"):
                    self._write_state(
                        state_path,
                        {
                            **base_state,
                            "stage": "staging",
                            "status": "failed_before_commit",
                            "error": str(exc),
                        },
                    )
                    raise
                raise
            except Exception as exc:
                self._write_state(
                    state_path,
                    {
                        **base_state,
                        "stage": "committing",
                        "status": "rolled_back",
                        "error": str(exc),
                    },
                )
                raise

    def _default_staging_scanner_factory(self, staged_store, staging_index: Path):
        from rag_knowledge.services.scanner import DirectoryScanner

        return DirectoryScanner(
            cfg=self._cfg,
            store=staged_store,
            index_path=staging_index,
            refresh_retrieval=False,
        )

    @staticmethod
    def _cleanup_staging_store(staged_store) -> None:
        if staged_store is None:
            return
        try:
            staged_store.clear()
        except Exception:
            pass

    @contextmanager
    def _exclusive_lock(self, lock_path: Path, started_at: str) -> Iterator[None]:
        payload = {
            "pid": os.getpid(),
            "started_at": started_at,
            "message": "knowledge base rebuild is running",
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        while True:
            try:
                fd = os.open(lock_path, flags)
                break
            except FileExistsError as exc:
                detail = self._read_json(lock_path)
                pid = (detail or {}).get("pid") if detail else None
                if pid and not self._pid_exists(pid):
                    lock_path.unlink(missing_ok=True)
                    continue
                raise RebuildAlreadyRunningError(
                    f"已有知识库重建任务正在运行: {detail or lock_path}"
                ) from exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    @staticmethod
    def _write_state(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            if os.name == "nt":
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION,
                    False,
                    int(pid),
                )
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                return False
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
