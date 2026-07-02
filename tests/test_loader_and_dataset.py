import unittest
import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from langchain_core.documents import Document

from rag_knowledge.evaluation.test_dataset import build_hardcase_dataset
from rag_knowledge.repository.vector_store import VectorStore


def _load_file_loader():
    stub = ModuleType("rag_knowledge.services.unstructured_loader")
    stub.UnstructuredChapterLoader = type("UnstructuredChapterLoader", (), {})
    stub.SUPPORTED_EXTS = {".txt", ".md", ".docx"}
    sys.modules.setdefault("rag_knowledge.services.unstructured_loader", stub)
    module = importlib.import_module("rag_knowledge.services.loader")
    return module.FileLoader


class LoaderCleaningTests(unittest.TestCase):
    def test_sanitize_text_removes_word_field_codes(self):
        FileLoader = _load_file_loader()
        raw = 'HYPERLINK "http://example.com"\n安装步骤\nPAGEREF _Toc123 \\h'
        cleaned = FileLoader._sanitize_text(raw)
        self.assertEqual(cleaned, "安装步骤")

    def test_post_process_filters_toc_and_keeps_real_content(self):
        FileLoader = _load_file_loader()
        loader = object.__new__(FileLoader)
        chunks = [
            Document(page_content="第一章 简介........ 1\n第二章 安装........ 2", metadata={}),
            Document(page_content="StampNodeServer 启动命令是 pm2 start app.js", metadata={}),
        ]

        result = loader._post_process_chunks(chunks)

        self.assertEqual(len(result), 1)
        self.assertIn("pm2 start", result[0].page_content)


class HardCaseDatasetTests(unittest.TestCase):
    def test_build_hardcase_dataset_expands_each_question(self):
        dataset = [{
            "question": "部署StampNodeServer时需要执行哪条命令？",
            "relevant_chunk_ids": ["chunk-1"],
            "kb_name": "文章附件",
            "doc_category": "",
            "source": "StampServer_V5_Rocky9_部署手册.docx",
        }]

        result = build_hardcase_dataset(dataset)

        self.assertEqual(len(result), 4)
        question_types = {item["question_type"] for item in result}
        self.assertEqual(question_types, {"standard", "colloquial", "typo_abbr", "version_ambiguous"})
        self.assertTrue(any("Rocky9" in item["question"] or "不同版本" in item["question"] for item in result))


class VectorStoreMetadataTests(unittest.TestCase):
    def test_safe_persist_path_uses_ascii_relative_path_inside_project(self):
        path = Path.cwd() / "chroma_db"
        self.assertEqual(VectorStore._safe_persist_path(path), "chroma_db")

    def test_clear_deletes_collection_without_removing_open_sqlite_directory(self):
        store = object.__new__(VectorStore)
        chroma = MagicMock()
        store._store = chroma
        store._persist_dir = "."

        store.clear()

        chroma.delete_collection.assert_called_once_with()
        self.assertIsNone(store._store)

    def test_update_metadata_merges_review_status(self):
        store = object.__new__(VectorStore)
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["chunk-1"],
            "metadatas": [{"kb_name": "文章附件", "review_status": "pending"}],
        }
        chroma = MagicMock()
        chroma._collection = collection
        store._get_store = MagicMock(return_value=chroma)

        updated = store.update_metadata(["chunk-1"], {"review_status": "approved"})

        self.assertEqual(updated, 1)
        collection.update.assert_called_once_with(
            ids=["chunk-1"],
            metadatas=[{"kb_name": "文章附件", "review_status": "approved"}],
        )

    def test_normalize_metadata_replaces_none_and_complex_values(self):
        normalized = VectorStore._normalize_metadata({
            "review_status": None,
            "page_number": 3,
            "tags": ["a", "b"],
        })

        self.assertEqual(normalized, {
            "review_status": "",
            "page_number": 3,
            "tags": "['a', 'b']",
        })


if __name__ == "__main__":
    unittest.main()
