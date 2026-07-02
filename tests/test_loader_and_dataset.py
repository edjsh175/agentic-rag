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

class ExcelLoaderTests(unittest.TestCase):
    """测试 Excel 相关功能：_rows_to_markdown / TEXT_EXTS / detect_category"""

    def setUp(self):
        self.FileLoader = _load_file_loader()
        # 从 loader 模块获取 TEXT_EXTS 常量
        import rag_knowledge.services.loader as loader_mod
        self.TEXT_EXTS = loader_mod.TEXT_EXTS

    # ------------------------------------------------------------------
    # _rows_to_markdown
    # ------------------------------------------------------------------

    def test_rows_to_markdown_typical_table(self):
        """典型二维表 → 生成合法 Markdown 表格（表头 + 分隔行 + 数据行）"""
        rows = [
            ("姓名", "部门", "工龄"),
            ("张三", "研发", 3),
            ("李四", "运维", 5),
        ]
        result = self.FileLoader._rows_to_markdown(rows)
        lines = result.strip().split("\n")
        self.assertEqual(len(lines), 4)                      # 表头 + 分隔 + 2 数据
        self.assertTrue(lines[0].startswith("| "))
        self.assertIn("---", lines[1])                       # 分隔行
        self.assertIn("张三", lines[2])
        self.assertIn("李四", lines[3])

    def test_rows_to_markdown_empty_returns_empty_string(self):
        """空 rows 列表 → 返回空字符串"""
        self.assertEqual(self.FileLoader._rows_to_markdown([]), "")

    def test_rows_to_markdown_single_header_only(self):
        """只有表头行，无数据行 → 仍输出表头和分隔行"""
        rows = [("A", "B", "C")]
        result = self.FileLoader._rows_to_markdown(rows)
        lines = result.strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("A", lines[0])
        self.assertIn("---", lines[1])

    def test_rows_to_markdown_pipe_chars_escaped(self):
        """单元格中含 | 符号时应被转义为 \\|"""
        rows = [
            ("key|value",),
            ("a|b",),
        ]
        result = self.FileLoader._rows_to_markdown(rows)
        self.assertNotIn(" | key|value |", result)          # 原始 | 不能出现在内容中
        self.assertIn("\\|", result)

    def test_rows_to_markdown_none_cells_become_empty(self):
        """None 单元格值 → 转为空字符串，不抛异常"""
        rows = [
            ("A", "B"),
            (None, "v2"),
        ]
        result = self.FileLoader._rows_to_markdown(rows)
        self.assertIn("v2", result)
        self.assertNotIn("None", result)

    def test_rows_to_markdown_short_rows_padded(self):
        """数据行列数少于表头时，补空列对齐，不抛异常"""
        rows = [
            ("A", "B", "C"),
            ("x",),         # 只有 1 列
        ]
        result = self.FileLoader._rows_to_markdown(rows)
        lines = result.strip().split("\n")
        # 最后一行应有 3 个管道符分隔的列（含补位空格）
        self.assertEqual(lines[2].count("|") - 1, 3)

    def test_rows_to_markdown_empty_header_cols(self):
        """全为空列的表头行 → 返回空字符串（cols == 0）"""
        self.assertEqual(self.FileLoader._rows_to_markdown([()]), "")

    # ------------------------------------------------------------------
    # TEXT_EXTS 与 detect_category
    # ------------------------------------------------------------------

    def test_xls_in_text_exts(self):
        """.xls 应在 TEXT_EXTS 集合中"""
        self.assertIn(".xls", self.TEXT_EXTS)

    def test_xlsx_in_text_exts(self):
        """.xlsx 应在 TEXT_EXTS 集合中"""
        self.assertIn(".xlsx", self.TEXT_EXTS)

    def test_detect_category_xls_returns_text(self):
        """detect_category 对 .xls 文件返回 FileCategory.TEXT"""
        from rag_knowledge.models.document import FileCategory
        result = self.FileLoader.detect_category("report.xls")
        self.assertEqual(result, FileCategory.TEXT)

    def test_detect_category_xlsx_returns_text(self):
        """detect_category 对 .xlsx 文件返回 FileCategory.TEXT"""
        from rag_knowledge.models.document import FileCategory
        result = self.FileLoader.detect_category("data.xlsx")
        self.assertEqual(result, FileCategory.TEXT)

    def test_detect_category_unsupported_returns_none(self):
        """detect_category 对不支持的扩展名仍返回 None"""
        result = self.FileLoader.detect_category("archive.zip")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
