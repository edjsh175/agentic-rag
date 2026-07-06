"""
测试 Conversation Contextualizer —— 对话式查询上下文化

覆盖场景：
1. 追问 "再详细说明一下" → 应改写成上一轮 Rocky Linux 9 镜像主题的独立查询
2. 独立问题 "Docker 报这个错怎么解决？" → 不应被强行绑定上一轮主题
3. 步骤引用 "第 5 步是什么意思？" → 应结合上一轮来源生成独立查询
"""
import unittest
from unittest.mock import patch

from rag_knowledge.services.query_contextualizer import QueryContextualizer


class QueryContextualizerTests(unittest.TestCase):
    """验证 contextualizer 的核心行为，不依赖真实 LLM 调用。"""

    def setUp(self):
        self.ctx = QueryContextualizer()
        self.ctx._contextualize_via_llm = unittest.mock.MagicMock(
            side_effect=RuntimeError("LLM disabled in unit tests")
        )

    # ------------------------------------------------------------------
    # 上下文依赖检测
    # ------------------------------------------------------------------

    def test_detect_context_dependent_followup(self):
        """追问型问题应被识别为依赖历史"""
        result = self.ctx._detect_context_dependence(
            question="再详细说明一下",
            history_text="user: Rocky Linux 9 虚拟机安装文档里，镜像是怎么找和下载的？\n"
                         "assistant: 镜像可以从阿里云镜像站下载，选择 Rocky Linux 9.5、isos、x86_64、minimal.iso",
        )
        self.assertTrue(result, "追问应被识别为依赖历史")

    def test_detect_context_independent_question(self):
        """独立完整的问题不应被识别为依赖历史"""
        result = self.ctx._detect_context_dependence(
            question="Docker 容器启动报 permission denied 怎么解决？",
            history_text="user: Rocky Linux 9 怎么安装？\n"
                         "assistant: 从阿里云镜像站下载 Rocky Linux 9.5 minimal.iso",
        )
        self.assertFalse(result, "独立问题不应被识别为依赖历史")

    def test_detect_step_reference(self):
        """步骤引用应被识别为依赖历史"""
        result = self.ctx._detect_context_dependence(
            question="第 5 步是什么意思？",
            history_text="user: 怎么配置网络？\n"
                         "assistant: 第 1 步安装系统，第 2 步配置用户...第 5 步配置静态 IP",
        )
        self.assertTrue(result, "步骤引用应被识别为依赖历史")

    # ------------------------------------------------------------------
    # 关键词提取与独立查询构建
    # ------------------------------------------------------------------

    def test_build_standalone_from_followup(self):
        """追问应基于上一轮摘要构建独立查询"""
        standalone = self.ctx._build_standalone_query(
            question="再详细说明一下",
            last_assistant="镜像可以从阿里云镜像站下载，选择 Rocky Linux 9.5、isos、x86_64、minimal.iso",
            last_user="Rocky Linux 9 虚拟机安装文档里，镜像是怎么找和下载的？",
            last_sources=[
                {"file_name": "0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md",
                 "section_title": "镜像下载"}
            ],
        )
        self.assertIsNotNone(standalone)
        self.assertGreater(len(standalone), 10)
        # 必须包含上一轮主题关键词
        self.assertTrue(
            any(kw in standalone.lower() for kw in ["rocky", "linux", "镜像", "下载", "阿里云"]),
            f"standalone_query 必须包含上一轮主题关键词，实际: {standalone}"
        )
        # 不能只返回追问原文
        self.assertNotEqual(standalone.strip(), "再详细说明一下")

    def test_preserve_independent_question(self):
        """独立问题应保持原样或微调，不应被绑定到无关主题"""
        standalone = self.ctx._build_standalone_query(
            question="Docker 容器启动报 permission denied 怎么解决？",
            last_assistant="镜像从阿里云镜像站下载 Rocky Linux 9.5 minimal.iso",
            last_user="Rocky Linux 9 怎么安装？",
            last_sources=[{"file_name": "rocky-linux-install.md"}],
        )
        self.assertIsNotNone(standalone)
        # 不应包含上一轮主题词
        self.assertFalse(
            all(kw in standalone.lower() for kw in ["rocky", "linux 9"]),
            f"独立问题不应被绑定到上一轮 Rocky Linux 主题，实际: {standalone}"
        )
        # 应包含 Docker 相关关键词
        self.assertTrue(
            "docker" in standalone.lower() or "permission" in standalone.lower(),
            f"应保留 Docker 相关关键词，实际: {standalone}"
        )

    def test_standalone_not_empty_for_short_input(self):
        """即使是极短输入也应返回有效的独立查询"""
        standalone = self.ctx._build_standalone_query(
            question="为什么？",
            last_assistant="建议使用 minimal.iso 而不是 DVD 版本，因为体积更小。",
            last_user="Rocky Linux 9 应该用哪个 ISO？",
            last_sources=[],
        )
        self.assertIsNotNone(standalone)
        self.assertGreater(len(standalone), 5)
        self.assertNotEqual(standalone.strip(), "为什么？")

    # ------------------------------------------------------------------
    # 来源摘要构建
    # ------------------------------------------------------------------

    def test_extract_source_summary_from_docs(self):
        """从 source_documents 中提取摘要"""
        sources = [
            {"metadata": {"file_name": "install-rocky.md", "section_title": "下载镜像",
                          "page_label": "3"}},
            {"metadata": {"file_name": "network-config.md", "section_title": "静态IP配置",
                          "page_label": "12"}},
        ]
        summary = self.ctx.extract_source_summary(sources)
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0]["file_name"], "install-rocky.md")
        self.assertEqual(summary[0]["section_title"], "下载镜像")
        self.assertEqual(summary[1]["section_title"], "静态IP配置")

    def test_extract_source_summary_empty(self):
        """空来源应返回空列表"""
        self.assertEqual(self.ctx.extract_source_summary([]), [])
        self.assertEqual(self.ctx.extract_source_summary(None), [])

    # ------------------------------------------------------------------
    # 搜索查询扩展
    # ------------------------------------------------------------------

    def test_generate_search_queries(self):
        """应生成多个搜索角度"""
        queries = self.ctx._generate_search_queries(
            standalone_query="Rocky Linux 9 镜像下载 阿里云镜像站 x86_64 minimal.iso",
        )
        self.assertIsInstance(queries, list)
        self.assertGreater(len(queries), 0, "应至少生成一个搜索查询")

    # ------------------------------------------------------------------
    # 完整输入构建
    # ------------------------------------------------------------------

    def test_build_contextualizer_input_minimal(self):
        """最小输入也应能构建有效的 contextualizer 输入"""
        result = self.ctx.contextualize(
            question="再详细说明一下",
            history=[
                {"role": "user", "content": "Rocky Linux 9 镜像怎么下载？"},
                {"role": "assistant", "content": "从阿里云镜像站下载 minimal.iso",
                 "sources": [{"file_name": "rocky-install.md", "section_title": "下载镜像"}]},
            ],
        )
        self.assertIn("standalone_query", result)
        self.assertIn("search_queries", result)
        self.assertIn("is_context_dependent", result)
        self.assertIn("confidence", result)
        self.assertIsInstance(result["search_queries"], list)


class MultiQueryBuildTests(unittest.TestCase):
    """验证 build_multi_queries() 多角度查询生成。"""

    def setUp(self):
        self.ctx = QueryContextualizer()
        self.ctx._contextualize_via_llm = unittest.mock.MagicMock(
            side_effect=RuntimeError("LLM disabled in unit tests")
        )

    def test_no_history_returns_original_question(self):
        """无历史时只返回原始问题"""
        queries = self.ctx.build_multi_queries(
            question="Rocky Linux 9 怎么安装？",
            history=None,
        )
        self.assertGreaterEqual(len(queries), 1)
        self.assertIn("Rocky Linux 9 怎么安装？", queries)

    def test_followup_generates_multiple_angles(self):
        """追问场景：应生成多角度查询（原始+改写+来源锚点+上一轮主题）"""
        history = [
            {"role": "user", "content": "Rocky Linux 9 虚拟机安装文档里，镜像是怎么找和下载的？"},
            {"role": "assistant", "content": "镜像可以从阿里云镜像站下载，选择 Rocky Linux 9.5、isos、x86_64、minimal.iso",
             "sources": [
                 {"file_name": "0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md",
                  "section_title": "镜像下载"}
             ]},
        ]
        queries = self.ctx.build_multi_queries(
            question="再详细说明一下",
            history=history,
        )
        self.assertGreater(len(queries), 1, f"追问应生成多角度查询，实际只有 {len(queries)}: {queries}")

        # 至少有一个查询包含来源文件名关键词
        all_text = " ".join(queries).lower()
        self.assertTrue(
            any(kw in all_text for kw in ["rocky", "linux", "镜像", "安装"]),
            f"多查询中应包含上一轮主题关键词，实际: {queries}"
        )

    def test_source_anchor_from_file_name(self):
        """来源锚点查询：从文件名提取可检索关键词"""
        history = [
            {"role": "user", "content": "怎么配置网络？"},
            {"role": "assistant", "content": "编辑 /etc/sysconfig/network-scripts/ifcfg-eth0",
             "sources": [
                 {"file_name": "0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md",
                  "section_title": "网络配置"},
                 {"file_name": "network-troubleshooting-guide.md",
                  "section_title": "静态IP设置"},
             ]},
        ]
        queries = self.ctx.build_multi_queries(
            question="第5步是什么意思？",
            history=history,
        )
        all_text = " ".join(queries).lower()
        # 应包含文件名中提取的关键词
        self.assertTrue(
            "rockylinux" in all_text.replace(" ", "") or "linux" in all_text or "网络" in all_text,
            f"来源锚点应贡献检索关键词，实际: {queries}"
        )

    def test_previous_user_topic_as_anchor(self):
        """上一轮用户问题应作为主题锚点出现在查询中"""
        history = [
            {"role": "user", "content": "Docker Compose 如何配置网络？"},
            {"role": "assistant", "content": "使用 networks 字段定义自定义网络",
             "sources": [{"file_name": "docker-compose-guide.md", "section_title": "网络配置"}]},
        ]
        queries = self.ctx.build_multi_queries(
            question="再详细说明一下",
            history=history,
        )
        # 上一轮用户问题应被包含
        found = any("Docker Compose" in q for q in queries)
        self.assertTrue(found, f"上一轮用户问题应作为锚点，实际: {queries}")

    def test_independent_question_stays_focused(self):
        """独立问题不应被过多无关查询稀释"""
        history = [
            {"role": "user", "content": "Rocky Linux 9 怎么安装？"},
            {"role": "assistant", "content": "从阿里云镜像站下载 minimal.iso",
             "sources": [{"file_name": "rocky-install.md"}]},
        ]
        queries = self.ctx.build_multi_queries(
            question="Docker 容器启动报 permission denied 怎么解决？",
            history=history,
        )
        # 主查询应保留 Docker 问题
        all_text = " ".join(queries)
        self.assertIn("Docker", all_text)
        self.assertIn("permission", all_text.lower() or "permission", all_text)

    def test_deduplication_removes_duplicates(self):
        """重复查询应被去重"""
        history = [
            {"role": "user", "content": "Rocky Linux 9 镜像下载"},
            {"role": "assistant", "content": "从阿里云镜像站下载",
             "sources": [{"file_name": "rocky-install.md", "section_title": "镜像下载"}]},
        ]
        queries = self.ctx.build_multi_queries(
            question="Rocky Linux 9 镜像下载",
            history=history,
        )
        # 不应有明显重复
        lower_queries = [q.lower() for q in queries]
        self.assertEqual(len(lower_queries), len(set(lower_queries)),
                         f"查询列表包含重复: {queries}")

    def test_max_six_queries(self):
        """最多返回 6 个查询，避免过度检索"""
        history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1",
             "sources": [
                 {"file_name": "doc1.md", "section_title": "S1"},
                 {"file_name": "doc2.md", "section_title": "S2"},
                 {"file_name": "doc3.md", "section_title": "S3"},
             ]},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2",
             "sources": [{"file_name": "doc4.md", "section_title": "S4"}]},
        ]
        queries = self.ctx.build_multi_queries(
            question="再详细说明一下",
            history=history,
        )
        self.assertLessEqual(len(queries), 6,
                             f"最多 6 个查询，实际: {len(queries)}")

    def test_independent_rocky_question_rejects_dirty_history_anchors(self):
        history = [
            {"role": "user", "content": "再详细说明一下"},
            {"role": "assistant", "content": "当前知识库中未查询到相关内容。", "sources": [
                {"file_name": "MySQL数据库_数据类型与表约束.md"},
                {"file_name": "东方通用户手册_Rocky9.doc"},
                {"file_name": "StampTools用户手册.docx"},
            ]},
        ]
        llm_result = {
            "standalone_query": "Rocky Linux 9 虚拟机安装文档中镜像的查找和下载流程是什么？",
            "search_queries": ["Rocky Linux 9 镜像 下载", "Rocky Linux ISO 查找"],
            "is_context_dependent": True,
            "confidence": 0.95,
        }
        with patch.object(self.ctx, "contextualize", return_value=llm_result):
            queries = self.ctx.build_multi_queries(
                "Rocky Linux 9 虚拟机安装文档里，镜像是怎么找和下载的？", history
            )

        joined = " ".join(queries)
        self.assertNotIn("MySQL", joined)
        self.assertNotIn("东方通", joined)
        self.assertNotIn("StampTools", joined)
        self.assertLessEqual(len(queries), 3)

    def test_llm_dependent_cannot_override_independent_heuristic(self):
        history = [
            {"role": "user", "content": "Rocky Linux 9 怎么安装？"},
            {"role": "assistant", "content": "安装说明", "sources": [
                {"file_name": "rocky-install.md"}
            ]},
        ]
        llm_result = {
            "standalone_query": "Docker permission denied 解决方法",
            "search_queries": ["Docker 权限错误", "容器启动权限"],
            "is_context_dependent": True,
            "confidence": 0.99,
        }
        with patch.object(self.ctx, "contextualize", return_value=llm_result):
            specs = self.ctx.build_query_specs(
                "Docker 容器启动报 permission denied 怎么解决？", history
            )

        self.assertEqual([spec.kind for spec in specs], ["original", "standalone", "search"])
        self.assertEqual([spec.weight for spec in specs], [1.0, 0.8, 0.6])

    def test_dependent_followup_includes_low_weight_history_anchors(self):
        history = [
            {"role": "user", "content": "Rocky Linux 9 镜像怎么找？"},
            {"role": "assistant", "content": "下载说明", "sources": [
                {"file_name": "rocky-install.md", "section_title": "镜像下载"}
            ]},
        ]
        llm_result = {
            "standalone_query": "详细说明 Rocky Linux 9 镜像下载流程",
            "search_queries": ["Rocky Linux 9 ISO 下载"],
            "is_context_dependent": True,
            "confidence": 0.9,
        }
        with patch.object(self.ctx, "contextualize", return_value=llm_result):
            specs = self.ctx.build_query_specs("再详细说明一下", history)

        kinds = [spec.kind for spec in specs]
        self.assertIn("source_anchor", kinds)
        self.assertIn("last_user", kinds)
        self.assertEqual(next(spec.weight for spec in specs if spec.kind == "source_anchor"), 0.3)


class SourceAnchorTests(unittest.TestCase):
    """来源锚点查询生成测试。"""

    def setUp(self):
        self.ctx = QueryContextualizer()

    def test_clean_filename_removes_hash_prefix(self):
        """文件名清洗：去掉哈希前缀和扩展名"""
        from rag_knowledge.services.query_contextualizer import _clean_filename_for_query
        result = _clean_filename_for_query(
            "0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md"
        )
        self.assertNotIn("0e57a89", result)
        self.assertNotIn(".md", result)
        self.assertIn("Linux", result)
        self.assertIn("rockyLinux", result.replace(" ", ""))

    def test_clean_filename_handles_plain_names(self):
        """文件名清洗：普通文件名保持不变"""
        from rag_knowledge.services.query_contextualizer import _clean_filename_for_query
        result = _clean_filename_for_query("docker-compose-guide.md")
        self.assertEqual(result, "docker compose guide")

    def test_build_source_anchors_combines_file_and_section(self):
        """来源锚点：文件名 + 章节标题组合"""
        anchors = self.ctx._build_source_anchor_queries([
            {"file_name": "install-guide.md", "section_title": "下载镜像"},
            {"file_name": "network-config.md", "section_title": "静态IP"},
        ])
        self.assertEqual(len(anchors), 2)
        self.assertIn("install guide 下载镜像", anchors)
        self.assertIn("network config 静态IP", anchors)

    def test_build_source_anchors_empty(self):
        """空来源返回空列表"""
        self.assertEqual(self.ctx._build_source_anchor_queries([]), [])
        self.assertEqual(self.ctx._build_source_anchor_queries(None), [])
