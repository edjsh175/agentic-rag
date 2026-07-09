from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyError


class RebuildAlreadyRunningError(RuntimeError):
    pass


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
    ):
        self._cfg = cfg
        self._store = store
        self._scanner = scanner
        self._consistency_service = consistency_service
        self._invalidate_retrieval_caches = invalidate_retrieval_caches
        self._rebuild_bm25 = rebuild_bm25

    def run(self) -> dict:
        data_dir = Path(self._cfg.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        state_path = data_dir / "rebuild_state.json"
        lock_path = data_dir / "rebuild.lock"
        index_path = data_dir / "file_index.json"
        started_at = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = ""

        with self._exclusive_lock(lock_path, started_at):
            if index_path.exists():
                backup = data_dir / f"file_index.backup-{started_at}.json"
                shutil.copy2(index_path, backup)
                backup_path = str(backup)

            self._write_state(
                state_path,
                {"status": "running", "started_at": started_at, "backup_path": backup_path},
            )

            try:
                self._store.clear()
                self._invalidate_retrieval_caches("rebuild_clear")
                self._scanner.reset_index()
                result = self._scanner.scan()
                self._consistency_service.assert_consistent()
                self._rebuild_bm25()
                self._invalidate_retrieval_caches("rebuild_scan")
                if state_path.exists():
                    state_path.unlink()
                return {
                    "message": "知识库已重建",
                    "backup_path": backup_path,
                    "new_files": result["new_files"],
                    "skipped_files": result["skipped_files"],
                    "errors": result["errors"],
                }
            except KnowledgeBaseConsistencyError as exc:
                self._write_state(
                    state_path,
                    {
                        "status": "failed",
                        "started_at": started_at,
                        "backup_path": backup_path,
                        "error": str(exc),
                        "report": exc.report,
                    },
                )
                raise
            except Exception as exc:
                self._write_state(
                    state_path,
                    {
                        "status": "failed",
                        "started_at": started_at,
                        "backup_path": backup_path,
                        "error": str(exc),
                    },
                )
                raise

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
