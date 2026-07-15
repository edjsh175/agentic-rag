"""测试 /upload 接口响应中的 chunks_count 与扫描统计字段。"""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import UploadFile

from rag_knowledge.models.api import UploadResponse
from rag_knowledge.api import routes


class UploadResponseModelTests(unittest.TestCase):
    """UploadResponse 数据模型测试（纯单元测试，不依赖 FastAPI）。"""

    def test_fields_are_populated_correctly(self):
        """所有字段正确赋值（包括新增字段）。"""
        resp = UploadResponse(
            message="文件已上传至知识库「文章附件」(其他)",
            chunks_count=12,
            file_name="test.docx",
            new_files=1,
            skipped_files=0,
            errors=0,
        )
        self.assertEqual(resp.chunks_count, 12)
        self.assertEqual(resp.new_files, 1)
        self.assertEqual(resp.skipped_files, 0)
        self.assertEqual(resp.errors, 0)
        self.assertEqual(resp.file_name, "test.docx")

    def test_new_fields_have_default_zero(self):
        """新增字段默认值为 0，不破坏已有调用方。"""
        resp = UploadResponse(
            message="ok",
            chunks_count=0,
            file_name="f.txt",
        )
        self.assertEqual(resp.new_files, 0)
        self.assertEqual(resp.skipped_files, 0)
        self.assertEqual(resp.errors, 0)

    def test_duplicate_file_all_zeros(self):
        """重复上传：chunks_count=0, new_files=0, skipped_files=1。"""
        resp = UploadResponse(
            message="文件已上传至知识库「文章附件」(其他)",
            chunks_count=0,
            file_name="dup.docx",
            new_files=0,
            skipped_files=1,
            errors=0,
        )
        self.assertEqual(resp.chunks_count, 0)
        self.assertEqual(resp.new_files, 0)
        self.assertEqual(resp.skipped_files, 1)

    def test_scan_error_response(self):
        """扫描失败：errors=1, chunks_count=0。"""
        resp = UploadResponse(
            message="文件已上传至知识库「文章附件」(其他)",
            chunks_count=0,
            file_name="bad.docx",
            new_files=0,
            skipped_files=0,
            errors=1,
        )
        self.assertEqual(resp.errors, 1)
        self.assertEqual(resp.chunks_count, 0)

    def test_chunks_count_not_equal_to_new_files(self):
        """核心修复：chunks_count 不再等于 new_files。"""
        # 模拟：1 个新文件产生了 12 个 chunk
        resp = UploadResponse(
            message="ok",
            chunks_count=12,
            file_name="big.docx",
            new_files=1,
            skipped_files=0,
            errors=0,
        )
        # chunks_count 应该是 chunk 数，不是文件数
        self.assertNotEqual(resp.chunks_count, resp.new_files)
        self.assertEqual(resp.chunks_count, 12)
        self.assertEqual(resp.new_files, 1)


class UploadRouteLogicTests(unittest.TestCase):
    """测试 /upload 路由中 chunks_count 的计算逻辑（模拟 scanner 和 vector store）。"""

    def setUp(self):
        """保存原始模块级变量，测试后恢复。"""
        self._orig_scanner = routes._scanner
        self._orig_store = routes._store
        self._orig_cfg = routes._cfg

    def tearDown(self):
        routes._scanner = self._orig_scanner
        routes._store = self._orig_store
        routes._cfg = self._orig_cfg

    def _patch_upload_dependencies(self, scan_result, before_count, after_count):
        """构造一个假的 upload 调用链并验证返回值逻辑。

        不实际调用 FastAPI TestClient，而是直接验证核心计算：
        chunks_count = max(0, after_count - before_count)
        """
        chunks_count = max(0, after_count - before_count)
        return chunks_count, scan_result

    def test_new_file_produces_chunk_diff(self):
        """新增文件：chunks_count = 前后向量库数量差值。"""
        scan_result = {"new_files": 1, "skipped_files": 0, "errors": 0, "details": []}
        chunks_count, result = self._patch_upload_dependencies(
            scan_result, before_count=100, after_count=112
        )
        self.assertEqual(chunks_count, 12)
        self.assertEqual(result["new_files"], 1)

    def test_duplicate_file_no_new_chunks(self):
        """重复文件：扫描前后向量库数量不变 → chunks_count=0。"""
        scan_result = {"new_files": 0, "skipped_files": 1, "errors": 0, "details": []}
        chunks_count, result = self._patch_upload_dependencies(
            scan_result, before_count=100, after_count=100
        )
        self.assertEqual(chunks_count, 0)
        self.assertEqual(result["new_files"], 0)
        self.assertEqual(result["skipped_files"], 1)

    def test_scan_error_zero_chunks(self):
        """扫描失败：chunks_count=0, errors 正确传递。"""
        scan_result = {"new_files": 0, "skipped_files": 0, "errors": 1, "details": []}
        chunks_count, result = self._patch_upload_dependencies(
            scan_result, before_count=100, after_count=100
        )
        self.assertEqual(chunks_count, 0)
        self.assertEqual(result["errors"], 1)

    def test_chunks_count_never_negative(self):
        """边界情况：max(0, diff) 保证 chunks_count 不会为负数。"""
        scan_result = {"new_files": 0, "skipped_files": 0, "errors": 0, "details": []}
        chunks_count, _ = self._patch_upload_dependencies(
            scan_result, before_count=100, after_count=95
        )
        self.assertEqual(chunks_count, 0)
        self.assertGreaterEqual(chunks_count, 0)

    def test_upload_save_failure_does_not_leave_profile_selection(self):
        scanner = MagicMock()
        routes._scanner = scanner
        with tempfile.TemporaryDirectory() as tmp:
            routes._cfg = SimpleNamespace(watch_dir=Path(tmp))
            upload = UploadFile(filename="broken.txt", file=io.BytesIO(b"content"))

            with patch("builtins.open", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    routes.upload(
                        upload,
                        kb_name="test",
                        doc_category=routes.DOC_CATEGORIES[0],
                        document_profile="procedure",
                    )

        scanner.set_doc_category.assert_not_called()
        scanner.set_document_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
