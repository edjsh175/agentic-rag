import unittest
import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def _load_directory_scanner():
    vector_store_stub = ModuleType("rag_knowledge.repository.vector_store")
    vector_store_stub.VectorStore = MagicMock
    sys.modules["rag_knowledge.repository.vector_store"] = vector_store_stub

    stub = ModuleType("rag_knowledge.services.unstructured_loader")
    stub.UnstructuredChapterLoader = type("UnstructuredChapterLoader", (), {})
    stub.SUPPORTED_EXTS = {".txt", ".md", ".docx"}
    sys.modules.setdefault("rag_knowledge.services.unstructured_loader", stub)
    sys.modules.pop("rag_knowledge.services.scanner", None)
    module = importlib.import_module("rag_knowledge.services.scanner")
    return module.DirectoryScanner


class ScannerDocCategoryTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("rag_knowledge.repository.vector_store", None)
        sys.modules.pop("rag_knowledge.services.scanner", None)
        super().tearDown()

    def test_resolve_doc_category_prefers_explicit_map(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._dc_map = {"upload/test.docx": "后端开发"}
        scanner._rebuild_dc_map = {"upload/test.docx": "运维管理"}
        scanner._rebuild_hash_dc_map = {"hash-1": "前端开发"}
        scanner._save_dc_map = lambda: None

        result = scanner._resolve_doc_category(Path("upload/test.docx"), "upload/test.docx", "hash-1")

        self.assertEqual(result, "后端开发")

    def test_resolve_doc_category_inherits_from_rebuild_index(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._dc_map = {}
        scanner._rebuild_dc_map = {"upload/test.docx": "运维管理"}
        scanner._rebuild_hash_dc_map = {}
        scanner._save_dc_map = lambda: None

        result = scanner._resolve_doc_category(Path("upload/test.docx"), "upload/test.docx", "hash-1")

        self.assertEqual(result, "运维管理")

    def test_resolve_doc_category_uses_folder_name_then_fallback(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._dc_map = {}
        scanner._rebuild_dc_map = {}
        scanner._rebuild_hash_dc_map = {}
        scanner._save_dc_map = lambda: None

        inferred = scanner._resolve_doc_category(Path("运维管理/手册.docx"), "运维管理/手册.docx", "hash-1")
        fallback = scanner._resolve_doc_category(Path("upload/手册.docx"), "upload/手册.docx", "hash-2")

        self.assertEqual(inferred, "运维管理")
        self.assertEqual(fallback, "其他")


if __name__ == "__main__":
    unittest.main()
