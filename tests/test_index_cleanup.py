import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


def _stub_unstructured_loader():
    stub = ModuleType("rag_knowledge.services.unstructured_loader")
    stub.UnstructuredChapterLoader = type("UnstructuredChapterLoader", (), {})
    stub.SUPPORTED_EXTS = {".txt", ".md", ".docx"}
    sys.modules.setdefault("rag_knowledge.services.unstructured_loader", stub)


def _stub_vector_store_module():
    stub = ModuleType("rag_knowledge.repository.vector_store")
    stub.VectorStore = MagicMock
    sys.modules["rag_knowledge.repository.vector_store"] = stub


def _import_routes_module():
    _stub_unstructured_loader()
    _stub_vector_store_module()

    for name in (
        "rag_knowledge.api.routes",
        "rag_knowledge.services.rag",
        "rag_knowledge.services.scanner",
        "rag_knowledge.services.blog_syncer",
        "rag_knowledge.services.blog_crawler",
        "rag_knowledge.services.chat_storage",
        "rag_knowledge.services.agent_service",
    ):
        sys.modules.pop(name, None)

    rag_stub = ModuleType("rag_knowledge.services.rag")
    rag_stub.RagChain = type("RagChain", (), {})
    sys.modules["rag_knowledge.services.rag"] = rag_stub

    scanner_stub = ModuleType("rag_knowledge.services.scanner")
    scanner_stub.DirectoryScanner = type("DirectoryScanner", (), {})
    sys.modules["rag_knowledge.services.scanner"] = scanner_stub

    syncer_stub = ModuleType("rag_knowledge.services.blog_syncer")
    syncer_stub.BlogPostSyncer = type("BlogPostSyncer", (), {})
    sys.modules["rag_knowledge.services.blog_syncer"] = syncer_stub

    crawler_stub = ModuleType("rag_knowledge.services.blog_crawler")
    crawler_stub.create_crawler = MagicMock()
    crawler_stub.detect_platform = MagicMock(return_value="csdn")
    sys.modules["rag_knowledge.services.blog_crawler"] = crawler_stub

    chat_storage_stub = ModuleType("rag_knowledge.services.chat_storage")
    chat_storage_stub.ChatStorage = type("ChatStorage", (), {})
    sys.modules["rag_knowledge.services.chat_storage"] = chat_storage_stub

    agent_service_stub = ModuleType("rag_knowledge.services.agent_service")
    agent_service_stub.load_agents = MagicMock(return_value=[])
    sys.modules["rag_knowledge.services.agent_service"] = agent_service_stub

    return importlib.import_module("rag_knowledge.api.routes")


class IndexedFileCleanupTests(unittest.TestCase):
    def test_cleanup_deletes_vectors_and_index_entry(self):
        _stub_vector_store_module()
        from rag_knowledge.services.index_cleanup import cleanup_indexed_file

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            index_path = data_dir / "file_index.json"
            index_path.write_text(json.dumps({
                "version": 1,
                "files": {
                    "hash-1": {
                        "file_name": "demo.md",
                        "file_path": "docs/demo.md",
                        "chunk_ids": ["chunk-1", "chunk-2"],
                    }
                },
            }, ensure_ascii=False), encoding="utf-8")

            with patch("rag_knowledge.services.index_cleanup.VectorStore") as store_cls:
                result = cleanup_indexed_file("hash-1", data_dir=data_dir)

            store_cls.return_value.delete.assert_called_once_with(["chunk-1", "chunk-2"])
            self.assertTrue(result.index_removed)
            self.assertEqual(result.deleted_chunks, 2)
            self.assertTrue(result.should_rebuild_bm25)
            saved = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["files"], {})

    def test_cleanup_handles_missing_index_entry(self):
        _stub_vector_store_module()
        from rag_knowledge.services.index_cleanup import cleanup_indexed_file

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "file_index.json").write_text(
                json.dumps({"version": 1, "files": {}}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch("rag_knowledge.services.index_cleanup.VectorStore") as store_cls:
                result = cleanup_indexed_file("missing-hash", data_dir=data_dir)

            store_cls.return_value.delete.assert_not_called()
            self.assertFalse(result.index_removed)
            self.assertEqual(result.deleted_chunks, 0)
            self.assertFalse(result.should_rebuild_bm25)

    def test_cleanup_removes_index_without_rebuilding_when_chunk_ids_missing(self):
        _stub_vector_store_module()
        from rag_knowledge.services.index_cleanup import cleanup_indexed_file

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            index_path = data_dir / "file_index.json"
            index_path.write_text(json.dumps({
                "version": 1,
                "files": {
                    "hash-1": {
                        "file_name": "demo.md",
                        "file_path": "docs/demo.md",
                        "chunk_ids": [],
                    }
                },
            }, ensure_ascii=False), encoding="utf-8")

            with patch("rag_knowledge.services.index_cleanup.VectorStore") as store_cls:
                result = cleanup_indexed_file("hash-1", data_dir=data_dir)

            store_cls.return_value.delete.assert_not_called()
            self.assertTrue(result.index_removed)
            self.assertEqual(result.deleted_chunks, 0)
            self.assertFalse(result.should_rebuild_bm25)
            saved = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["files"], {})


class BlogDeletionIntegrationTests(unittest.TestCase):
    def test_blog_syncer_delete_file_vectors_uses_shared_cleanup(self):
        _stub_vector_store_module()
        sys.modules.pop("rag_knowledge.services.blog_syncer", None)
        module = importlib.import_module("rag_knowledge.services.blog_syncer")
        file_path = Path("watch/demo.md")

        with patch.object(module, "_hash_file", return_value="hash-1"), \
             patch.object(module, "cleanup_indexed_file") as cleanup:
            module._delete_file_vectors(file_path, Path("data"))

        cleanup.assert_called_once_with("hash-1", data_dir=Path("data"))

    def test_delete_blog_post_uses_shared_cleanup(self):
        routes = _import_routes_module()

        with tempfile.TemporaryDirectory() as tmp:
            blog_dir = Path(tmp)
            fp = blog_dir / "post.md"
            fp.write_text("content", encoding="utf-8")
            routes._cfg = SimpleNamespace(
                blog_publish_dir=blog_dir,
                blog_crawl_dir=blog_dir,
                data_dir=blog_dir,
            )

            cleanup_result = SimpleNamespace(should_rebuild_bm25=False)
            with patch.object(routes, "_hash_file", return_value="hash-1"), \
                 patch.object(routes, "cleanup_indexed_file", return_value=cleanup_result) as cleanup, \
                 patch.object(routes, "_rebuild_bm25") as rebuild:
                result = routes.delete_blog_post("post.md")

        cleanup.assert_called_once_with("hash-1", data_dir=blog_dir)
        rebuild.assert_not_called()
        self.assertEqual(result["message"], "已删除 post.md")


if __name__ == "__main__":
    unittest.main()
