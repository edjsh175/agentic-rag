"""Tests for content-based matching fallback in metrics.py."""
import hashlib
from rag_knowledge.evaluation.metrics import content_match, is_match_v2


class TestContentMatch:
    def test_fingerprint_match(self):
        text = "服务启动命令是 pm2 start app.js"
        fp = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        assert content_match({}, text, {"content_fingerprint": fp})

    def test_fingerprint_mismatch(self):
        assert not content_match({}, "some text", {"content_fingerprint": "0000000000000000"})

    def test_source_section_match(self):
        meta = {"source": "manual.docx", "section_path": "部署 > 启动"}
        target = {"source": "manual.docx", "section_path": "部署 > 启动"}
        assert content_match(meta, "", target)

    def test_source_section_match_with_keywords_mismatch(self):
        meta = {"source": "manual.docx", "section_path": "部署 > 启动"}
        target = {"source": "manual.docx", "section_path": "部署 > 启动", "keywords": ["pm2", "nginx"]}
        assert not content_match(meta, "完全无关的内容", target)

    def test_source_section_match_with_keywords_match(self):
        meta = {"source": "manual.docx", "section_path": "部署 > 启动"}
        target = {"source": "manual.docx", "section_path": "部署 > 启动", "keywords": ["pm2", "nginx"]}
        assert content_match(meta, "通过 pm2 启动 nginx 服务", target)

    def test_source_only_insufficient(self):
        meta = {"source": "manual.docx"}
        target = {"source": "manual.docx", "section_path": ""}
        assert not content_match(meta, "", target)

    def test_keywords_match_above_threshold(self):
        target = {"keywords": ["pm2", "启动", "服务", "命令", "部署"]}
        content = "服务启动命令是 pm2 start app.js，用于部署"
        assert content_match({}, content, target)  # 4/5 = 80% >= 60%

    def test_keywords_match_below_threshold(self):
        target = {"keywords": ["pm2", "启动", "服务", "命令", "部署"]}
        content = "完全无关的内容"
        assert not content_match({}, content, target)  # 0/5 = 0%

    def test_no_match(self):
        assert not content_match({}, "", {})


class TestIsMatchV2:
    def test_chunk_id_match_fast_path(self):
        assert is_match_v2("id-1", {}, "", {"id-1"}, [])

    def test_content_fallback(self):
        text = "服务启动命令"
        fp = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        assert is_match_v2("id-new", {}, text, {"id-old"}, [{"content_fingerprint": fp}])

    def test_neither_match(self):
        assert not is_match_v2("id-new", {}, "text", {"id-old"}, [{"content_fingerprint": "bad"}])
