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

    def test_document_profile_resolution_has_explicit_inherited_map_default_priority(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._profile_selection_map = {"upload/test.docx": "procedure"}
        scanner._rebuild_profile_map = {"upload/test.docx": "api_doc"}
        scanner._rebuild_hash_profile_map = {"hash-1": "table_doc"}
        scanner._profile_map = {"upload/test.docx": "record_list", "word/manual.docx": "technical_manual"}
        scanner._save_profile_selection_map = lambda: None

        explicit = scanner._resolve_document_profile("upload/test.docx", "hash-1")
        scanner._consume_document_profile_selection("upload/test.docx")
        inherited = scanner._resolve_document_profile("upload/test.docx", "hash-1")
        mapped = scanner._resolve_document_profile("word/manual.docx", "hash-2")
        default = scanner._resolve_document_profile("unknown.docx", "hash-3")

        self.assertEqual(explicit, "procedure")
        self.assertEqual(inherited, "api_doc")
        self.assertEqual(mapped, "technical_manual")
        self.assertEqual(default, "section_based")

    def test_document_profile_resolution_normalizes_windows_paths_and_reports_source(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._profile_selection_map = {}
        scanner._rebuild_profile_map = {}
        scanner._rebuild_hash_profile_map = {}
        scanner._profile_map = {"word/manual.docx": "technical_manual"}

        profile, source = scanner._resolve_document_profile_with_source(
            "word\\manual.docx", "hash-1"
        )

        self.assertEqual(profile, "technical_manual")
        self.assertEqual(source, "profile_map")

    def test_document_profile_selection_is_consumed_only_after_success(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._profile_selection_map = {"upload/test.docx": "procedure"}
        scanner._rebuild_profile_map = {}
        scanner._rebuild_hash_profile_map = {}
        scanner._profile_map = {}
        scanner._save_profile_selection_map = MagicMock()

        self.assertEqual(
            scanner._resolve_document_profile("upload/test.docx", "hash-1"),
            "procedure",
        )
        self.assertIn("upload/test.docx", scanner._profile_selection_map)

        scanner._consume_document_profile_selection("upload/test.docx")

        self.assertNotIn("upload/test.docx", scanner._profile_selection_map)
        scanner._save_profile_selection_map.assert_called_once_with()

    def test_set_document_profile_rejects_unknown_profile(self):
        DirectoryScanner = _load_directory_scanner()
        scanner = object.__new__(DirectoryScanner)
        scanner._profile_selection_map = {}
        scanner._save_profile_selection_map = lambda: None

        with self.assertRaises(ValueError):
            scanner.set_document_profile("upload/test.docx", "filename_magic")


if __name__ == "__main__":
    unittest.main()
