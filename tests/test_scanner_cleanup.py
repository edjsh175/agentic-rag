import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


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
            self.assertNotIn("valid.zip", file_names)


if __name__ == "__main__":
    unittest.main()
