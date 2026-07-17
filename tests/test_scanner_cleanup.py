import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

_INJECTED_UNSTRUCTURED_LOADER = False


def _load_scanner_module():
    global _INJECTED_UNSTRUCTURED_LOADER
    vector_store_stub = ModuleType("rag_knowledge.repository.vector_store")
    vector_store_stub.VectorStore = MagicMock
    sys.modules["rag_knowledge.repository.vector_store"] = vector_store_stub

    if "rag_knowledge.services.unstructured_loader" not in sys.modules:
        stub = ModuleType("rag_knowledge.services.unstructured_loader")
        stub.UnstructuredChapterLoader = type("UnstructuredChapterLoader", (), {})
        stub.SUPPORTED_EXTS = {".txt", ".md", ".docx"}
        sys.modules["rag_knowledge.services.unstructured_loader"] = stub
        _INJECTED_UNSTRUCTURED_LOADER = True
    sys.modules.pop("rag_knowledge.services.scanner", None)
    return importlib.import_module("rag_knowledge.services.scanner")


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    isolated_storage(
        db_name="scanner-cleanup.db",
        chroma_name="scanner-cleanup-chroma",
        data_dir_name="scanner-cleanup-data",
    )


class ScannerCleanupTests(unittest.TestCase):
    def tearDown(self):
        global _INJECTED_UNSTRUCTURED_LOADER
        sys.modules.pop("rag_knowledge.repository.vector_store", None)
        sys.modules.pop("rag_knowledge.services.scanner", None)
        sys.modules.pop("rag_knowledge.services.query_cache", None)
        if _INJECTED_UNSTRUCTURED_LOADER:
            sys.modules.pop("rag_knowledge.services.unstructured_loader", None)
            _INJECTED_UNSTRUCTURED_LOADER = False
        super().tearDown()

    def test_clean_removed_deletes_vectors_and_index_entry(self):
        module = _load_scanner_module()
        scanner = object.__new__(module.DirectoryScanner)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scanner._index = {
                "files": {
                    "hash-1": {
                        "file_name": "missing.md",
                        "file_path": "missing.md",
                        "chunk_ids": ["chunk-1"],
                    }
                }
            }
            scanner._cfg = SimpleNamespace(data_dir=base)

            results = scanner._clean_removed(base)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "missing.md")
        self.assertEqual(results[0].deleted_chunks, 1)
        self.assertEqual(scanner._index["files"], {})

    def test_scan_does_not_rebuild_bm25_when_no_changes(self):
        module = _load_scanner_module()
        scanner = object.__new__(module.DirectoryScanner)
        scanner._cfg = SimpleNamespace(watch_dir=Path("."), scan_interval=30)
        scanner._index = {"files": {}}
        scanner._refresh_retrieval = True
        scanner._collect_files = MagicMock(return_value=[])
        scanner._clean_removed = MagicMock(return_value=[])
        scanner._save_index = MagicMock()

        bm25 = MagicMock()
        module.BM25Store = MagicMock(return_value=bm25)

        result = scanner.scan()

        bm25.rebuild.assert_not_called()
        self.assertEqual(result["new_files"], 0)
        self.assertEqual(result["skipped_files"], 0)
        self.assertEqual(result["errors"], 0)

    def test_scan_clears_query_cache_when_index_changes(self):
        module = _load_scanner_module()
        scanner = object.__new__(module.DirectoryScanner)
        scanner._cfg = SimpleNamespace(watch_dir=Path("."), scan_interval=30)
        scanner._index = {"files": {}}
        scanner._refresh_retrieval = True
        scanner._collect_files = MagicMock(return_value=[])
        scanner._clean_removed = MagicMock(
            return_value=[SimpleNamespace(file_name="gone.md", should_rebuild_bm25=True)]
        )
        scanner._save_index = MagicMock()

        bm25 = MagicMock()
        module.BM25Store = MagicMock(return_value=bm25)

        query_cache_stub = ModuleType("rag_knowledge.services.query_cache")
        query_cache_stub.clear_query_cache = MagicMock()
        sys.modules["rag_knowledge.services.query_cache"] = query_cache_stub

        scanner.scan()

        query_cache_stub.clear_query_cache.assert_called_once_with()

    def test_staging_scan_skips_bm25_and_cache_when_refresh_retrieval_disabled(self):
        module = _load_scanner_module()
        scanner = object.__new__(module.DirectoryScanner)
        scanner._cfg = SimpleNamespace(watch_dir=Path("."), scan_interval=30)
        scanner._index = {"files": {}}
        scanner._refresh_retrieval = False
        scanner._collect_files = MagicMock(return_value=[])
        scanner._clean_removed = MagicMock(
            return_value=[SimpleNamespace(file_name="gone.md", should_rebuild_bm25=True)]
        )
        scanner._save_index = MagicMock()

        bm25 = MagicMock()
        module.BM25Store = MagicMock(return_value=bm25)

        query_cache_stub = ModuleType("rag_knowledge.services.query_cache")
        query_cache_stub.clear_query_cache = MagicMock()
        sys.modules["rag_knowledge.services.query_cache"] = query_cache_stub

        scanner.scan()

        bm25.rebuild.assert_not_called()
        query_cache_stub.clear_query_cache.assert_not_called()

    def test_scanner_uses_custom_index_path_for_persistence(self):
        module = _load_scanner_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            live_index = base / "file_index.json"
            staging_index = base / "rebuild" / "staging" / "file_index.json"
            staging_index.parent.mkdir(parents=True)
            live_index.write_text(
                '{"version":1,"files":{"h1":{"file_path":"a.md"}}}',
                encoding="utf-8",
            )

            scanner = module.DirectoryScanner(
                cfg=SimpleNamespace(
                    data_dir=base,
                    watch_dir=base,
                    watch_file_types=["md"],
                    scan_interval=30,
                ),
                loader=MagicMock(),
                index_path=staging_index,
                refresh_retrieval=False,
            )
            scanner._collect_files = MagicMock(return_value=[])
            scanner._clean_removed = MagicMock(return_value=[])
            scanner.scan()

            assert staging_index.exists()
            assert live_index.read_text(encoding="utf-8") == '{"version":1,"files":{"h1":{"file_path":"a.md"}}}'

    def test_reload_index_refreshes_in_memory_index(self):
        module = _load_scanner_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            index_path = base / "file_index.json"
            scanner = module.DirectoryScanner(
                cfg=SimpleNamespace(
                    data_dir=base,
                    watch_dir=base,
                    watch_file_types=["md"],
                    scan_interval=30,
                ),
                loader=MagicMock(),
                index_path=index_path,
            )
            scanner._index = {"version": 1, "files": {"old": {}}}
            index_path.write_text(
                '{"version":1,"files":{"new":{"file_path":"b.md"}}}',
                encoding="utf-8",
            )
            scanner.reload_index()
            self.assertIn("new", scanner._index["files"])
            self.assertNotIn("old", scanner._index["files"])

    def test_load_index_marks_legacy_profile_without_inventing_default(self):
        module = _load_scanner_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            index_path = base / "file_index.json"
            index_path.write_text(
                '{"version":1,"files":{"old":{"file_path":"a.docx"}}}',
                encoding="utf-8",
            )

            scanner = module.DirectoryScanner(
                cfg=SimpleNamespace(
                    data_dir=base,
                    watch_dir=base,
                    watch_file_types=["docx"],
                    scan_interval=30,
                ),
                loader=MagicMock(),
                index_path=index_path,
            )

            entry = scanner._index["files"]["old"]
            persisted = __import__("json").loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(scanner._index["version"], 3)
            self.assertNotIn("document_profile", entry)
            self.assertEqual(entry["document_profile_source"], "legacy")
            self.assertEqual(entry["chunk_policy_id"], "")
            self.assertEqual(persisted["version"], 3)

    def test_reset_index_does_not_inherit_untrusted_legacy_profile(self):
        module = _load_scanner_module()
        scanner = object.__new__(module.DirectoryScanner)
        scanner._index = {
            "version": 3,
            "files": {
                "legacy": {
                    "file_path": "word\\manual.docx",
                    "document_profile": "section_based",
                    "document_profile_source": "legacy",
                },
                "trusted": {
                    "file_path": "word\\api.docx",
                    "document_profile": "api_doc",
                    "document_profile_source": "profile_map",
                },
            },
        }
        scanner._save_index = lambda: None
        scanner._decision_store = MagicMock()

        scanner.reset_index()

        self.assertNotIn("word/manual.docx", scanner._rebuild_profile_map)
        self.assertNotIn("legacy", scanner._rebuild_hash_profile_map)
        self.assertEqual(scanner._rebuild_profile_map["word/api.docx"], "api_doc")
        self.assertEqual(scanner._rebuild_hash_profile_map["trusted"], "api_doc")
        self.assertEqual(scanner._index, {"version": 3, "files": {}})

    def test_collect_files_filters_temporary_and_hidden_files(self):
        module = _load_scanner_module()
        scanner = object.__new__(module.DirectoryScanner)
        scanner._cfg = SimpleNamespace(
            watch_file_types=["pdf", "docx", "doc", "txt", "md"]
        )

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Create valid file
            (base / "valid.docx").write_text("content", encoding="utf-8")
            # Create temporary lock file
            (base / "~$valid.docx").write_text("content", encoding="utf-8")
            # Create dot file
            (base / ".hidden.txt").write_text("content", encoding="utf-8")
            # Create unsupported file type
            (base / "valid.zip").write_text("content", encoding="utf-8")

            files = scanner._collect_files(base)
            file_names = {p.name for p in files}

            self.assertIn("valid.docx", file_names)
            self.assertNotIn("~$valid.docx", file_names)
            self.assertNotIn(".hidden.txt", file_names)
            self.assertIn("valid.zip", file_names)

    def test_scanner_decision_lifecycle_and_counts(self):
        module = _load_scanner_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            index_path = base / "file_index.json"
            dec_path = base / "ingestion_decisions.json"

            # Setup mock VectorStore
            store_mock = MagicMock()
            store_mock.add_chunks.return_value = ["chunk1"]

            # Setup mock loader
            loader_mock = MagicMock()
            from rag_knowledge.services.loader import FileLoadResult
            from rag_knowledge.services.document_support import make_decision
            from langchain_core.documents import Document

            pdf_result = FileLoadResult(
                chunks=[Document(page_content="page 1 text", metadata={"chunk_policy_id": "policy1"})],
                category="text",
                decisions=[make_decision(base / "test.pdf", status="queued", reason_code="PDF_PAGE_REQUIRES_OCR", file_hash="hash_pdf", locator="page:2")]
            )

            loader_mock.load_with_decisions.side_effect = lambda fp, **kwargs: (
                pdf_result if fp.endswith(".pdf") else (
                    FileLoadResult([], "text", [make_decision(fp, status="queued", reason_code="LEGACY_DOC_REQUIRES_CONVERSION", file_hash="hash_doc")])
                )
            )

            scanner = module.DirectoryScanner(
                cfg=SimpleNamespace(
                    data_dir=base,
                    watch_dir=base,
                    watch_file_types=["pdf", "docx"],
                    scan_interval=30,
                ),
                store=store_mock,
                loader=loader_mock,
                index_path=index_path,
                decision_path=dec_path,
                refresh_retrieval=False,
            )

            # Create pdf and legacy doc
            (base / "test.pdf").write_bytes(b"pdf data")
            (base / "legacy.doc").write_bytes(b"doc data")
            (base / "dependency.jar").write_bytes(b"jar data")

            # First scan
            r = scanner.scan()

            self.assertEqual(r["new_files"], 1)
            self.assertEqual(r["queued_files"], 2)
            self.assertEqual(r["excluded_files"], 1)
            self.assertEqual(r["errors"], 0)

            # Check decision store content
            scanner._decision_store.reload()
            decisions = list(scanner._decision_store.snapshot()["decisions"].values())
            self.assertEqual(len(decisions), 3)

            # Test relocation: move test.pdf to moved.pdf
            (base / "moved.pdf").write_bytes((base / "test.pdf").read_bytes())
            (base / "test.pdf").unlink()

            # Relocation happens in next scan with parse failure simulation
            loader_mock.load_with_decisions.side_effect = lambda fp, **kwargs: (
                Exception("corrupted pdf") if fp.endswith(".pdf") else pdf_result
            )

            r2 = scanner.scan()
            scanner._decision_store.reload()
            decisions = list(scanner._decision_store.snapshot()["decisions"].values())
            self.assertFalse(any(d["file_name"] == "test.pdf" for d in decisions))


if __name__ == "__main__":
    unittest.main()
