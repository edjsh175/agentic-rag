import unittest
import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


_INJECTED_UNSTRUCTURED_LOADER = False


def _load_directory_scanner():
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
    module = importlib.import_module("rag_knowledge.services.scanner")
    return module.DirectoryScanner


class ScannerDocCategoryTests(unittest.TestCase):
    def tearDown(self):
        global _INJECTED_UNSTRUCTURED_LOADER
        sys.modules.pop("rag_knowledge.repository.vector_store", None)
        sys.modules.pop("rag_knowledge.services.scanner", None)
        if _INJECTED_UNSTRUCTURED_LOADER:
            sys.modules.pop("rag_knowledge.services.unstructured_loader", None)
            _INJECTED_UNSTRUCTURED_LOADER = False
        super().tearDown()

    def test_resolve_doc_category_prefers_explicit_map(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._dc_map = {"upload/test.docx": "StampTools"}
        scanner._rebuild_dc_map = {"upload/test.docx": "StampServer"}
        scanner._rebuild_hash_dc_map = {"hash-1": "基础环境"}
        scanner._save_dc_map = lambda: None

        result = scanner._resolve_doc_category(Path("upload/test.docx"), "upload/test.docx", "hash-1")

        self.assertEqual(result, "StampTools")

    def test_resolve_doc_category_inherits_from_rebuild_index(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._dc_map = {}
        scanner._rebuild_dc_map = {"upload/test.docx": "StampServer"}
        scanner._rebuild_hash_dc_map = {}
        scanner._save_dc_map = lambda: None

        result = scanner._resolve_doc_category(Path("upload/test.docx"), "upload/test.docx", "hash-1")

        self.assertEqual(result, "StampServer")

    def test_resolve_doc_category_uses_product_name_blog_then_fallback(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._dc_map = {}
        scanner._rebuild_dc_map = {}
        scanner._rebuild_hash_dc_map = {}
        scanner._save_dc_map = lambda: None

        inferred = scanner._resolve_doc_category(Path("word/StampWebRTC用户手册.docx"), "word/StampWebRTC用户手册.docx", "hash-1")
        blog = scanner._resolve_doc_category(Path("已发布文章/post.md"), "已发布文章/post.md", "hash-2")
        fallback = scanner._resolve_doc_category(Path("upload/手册.docx"), "upload/手册.docx", "hash-3")

        self.assertEqual(inferred, "StampWebRTC")
        self.assertEqual(blog, "博客")
        self.assertEqual(fallback, "其他")


if __name__ == "__main__":
    unittest.main()
