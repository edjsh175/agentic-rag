import json
import shutil
import unittest
from pathlib import Path

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy
from tests.fixtures.pipeline_graph_facts import seed_pipeline_table_graph

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="routing-structured-boost.db",
        chroma_name="routing-structured-boost-chroma",
        data_dir_name="routing-structured-boost-data",
    )
    policies_src = PROJECT_ROOT / "data" / "retrieval_intent_policies.json"
    shutil.copy2(policies_src, data_dir / "retrieval_intent_policies.json")
    seed_pipeline_table_graph(RelationalDB())


class RoutingHeuristicTests(unittest.TestCase):
    def test_manual_queries_route_to_attachment_kb(self):
        chain = object.__new__(RagChain)
        self.assertIsNone(chain._route_query("管线点表规范"))
        self.assertIsNone(chain._route_query("PipelineBuilder 如何发布管线"))
        self.assertIsNone(chain._route_query("管线发布服务如何配置"))
        self.assertTrue(chain._is_table_oriented_query("PipelineBuilder 管线点表字段要求"))

    def test_blog_queries_route_to_published_kb(self):
        chain = object.__new__(RagChain)
        self.assertIsNone(chain._route_query("CSDN 博客里有 Rocky9 安装经验吗"))


class RetrievalStrategyStructuredBoostTests(unittest.TestCase):
    def test_structured_queries_get_table_friendly_suffix(self):
        self.assertEqual(
            RetrievalStrategy._augment_structured_query("管线点表规范"),
            "管线点表规范 点数据结构 管点编号 地面高程 字段名 说明 数据结构",
        )
        self.assertEqual(
            RetrievalStrategy._augment_structured_query("管点编号 地面高程 特征 附属物设施"),
            "管点编号 地面高程 特征 附属物设施",
        )

    def test_point_table_publish_query_gets_point_data_recall_hints(self):
        augmented = RetrievalStrategy._augment_structured_query("发布管线点表的要求")

        self.assertIn("点数据结构", augmented)
        self.assertIn("管点编号", augmented)
        self.assertIn("地面高程", augmented)

    def test_table_oriented_queries_boost_table_chunks(self):
        from rag_knowledge.config import Config
        from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy
        cfg = Config()
        strategy = RetrievalQualityStrategy(cfg)
        docs = [
            Document(
                page_content="# PipelineBuilder > 数据规范 > 管线点表\n\n记录管线特征和附属设施信息。",
                metadata={"chunk_id": "text", "rrf_score": 0.0328},
            ),
            Document(
                page_content=(
                    "# PipelineBuilder > 数据规范 > 管线点表\n\n"
                    "| 字段名 | 说明 |\n"
                    "| --- | --- |\n"
                    "| 管点编号 | 唯一识别码 |\n"
                    "| 地面高程 | 管线点地表面的高程 |\n"
                ),
                metadata={
                    "chunk_id": "table",
                    "rrf_score": 0.0315,
                    "content_type": "table",
                    "chunking_method": "table",
                },
            ),
        ]

        result = strategy.apply("管线点表规范", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "table")
        self.assertGreater(result[0].metadata.get("table_query_boost", 0.0), 0.0)

    def test_point_table_query_prefers_point_table_over_other_tables(self):
        from rag_knowledge.config import Config
        from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy
        cfg = Config()
        strategy = RetrievalQualityStrategy(cfg)
        docs = [
            Document(
                page_content="# PipelineBuilder > 数据规范 > 管线面表：\n\n| 字段名 | 说明 |\n| --- | --- |\n| 管面编号 | 唯一标识码 |",
                metadata={
                    "chunk_id": "face_table",
                    "rrf_score": 0.0319,
                    "content_type": "table",
                    "chunking_method": "table",
                    "searchable_text": "PipelineBuilder 数据规范 管线面表 字段名 说明 管面编号 唯一标识码",
                },
            ),
            Document(
                page_content="# PipelineBuilder > 数据规范 > 管线点表\n\n| 字段名 | 说明 |\n| --- | --- |\n| 管点编号 | 唯一识别码 |",
                metadata={
                    "chunk_id": "point_table",
                    "rrf_score": 0.0315,
                    "content_type": "table",
                    "chunking_method": "table",
                    "searchable_text": "PipelineBuilder 数据规范 管线点表 字段名 说明 管点编号 唯一识别码",
                },
            ),
        ]

        result = strategy.apply("PipelineBuilder 管线点表字段要求", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "point_table")

    def test_point_table_query_treats_child_point_data_as_same_family(self):
        from rag_knowledge.config import Config
        from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy
        cfg = Config()
        strategy = RetrievalQualityStrategy(cfg)
        docs = [
            Document(
                page_content="# PipelineBuilder > 数据规范 > 面表数据结构\n\n| 字段名 | 说明 |\n| 管面编号 | 唯一标识码 |",
                metadata={
                    "chunk_id": "face_data",
                    "rrf_score": 0.04,
                    "content_type": "table",
                    "chunking_method": "table",
                    "section_path": "PipelineBuilder > 数据规范 > 面表数据结构",
                    "searchable_text": "PipelineBuilder 数据规范 面表数据结构 字段名 说明 管面编号",
                },
            ),
            Document(
                page_content="# PipelineBuilder > 数据规范 > 点数据结构\n\n| 字段名 | 说明 |\n| 管点编号 | 唯一识别码 |\n| 地面高程 | 管线点地表面的高程 |",
                metadata={
                    "chunk_id": "point_data",
                    "rrf_score": 0.035,
                    "content_type": "table",
                    "chunking_method": "table",
                    "section_path": "PipelineBuilder > 数据规范 > 点数据结构",
                    "searchable_text": "PipelineBuilder 数据规范 点数据结构 字段名 说明 管点编号 地面高程",
                },
            ),
        ]

        result = strategy.apply("PipelineBuilder 管线点表字段要求", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "point_data")
        self.assertGreater(result[0].metadata.get("intent_profile_boost", 0.0), 0.0)

    def test_line_table_with_point_fields_is_not_point_table_family(self):
        from rag_knowledge.config import Config
        from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy
        cfg = Config()
        strategy = RetrievalQualityStrategy(cfg)
        docs = [
            Document(
                page_content="# PipelineBuilder > 数据规范 > 线表数据结构\n\n| 字段名 | 说明 |\n| 起点编号 | 管点编号，全局唯一 |",
                metadata={
                    "chunk_id": "line_data",
                    "rrf_score": 0.04,
                    "content_type": "table",
                    "chunking_method": "table",
                    "section_path": "PipelineBuilder > 数据规范 > 线表数据结构",
                    "searchable_text": "PipelineBuilder 数据规范 线表数据结构 字段名 说明 起点编号 管点编号",
                },
            ),
            Document(
                page_content="# PipelineBuilder > 数据规范 > 点数据结构\n\n| 字段名 | 说明 |\n| 管点编号 | 唯一识别码 |\n| 地面高程 | 管线点地表面的高程 |",
                metadata={
                    "chunk_id": "point_data",
                    "rrf_score": 0.035,
                    "content_type": "table",
                    "chunking_method": "table",
                    "section_path": "PipelineBuilder > 数据规范 > 点数据结构",
                    "searchable_text": "PipelineBuilder 数据规范 点数据结构 字段名 说明 管点编号 地面高程",
                },
            ),
        ]

        result = strategy.apply("发布管线点表的要求", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "point_data")

    def test_dom_builder_publish_query_prefers_tool_workflow_over_service_publish(self):
        from rag_knowledge.config import Config
        from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy
        cfg = Config()
        strategy = RetrievalQualityStrategy(cfg)
        docs = [
            Document(
                page_content="# 3Dtiles数据服务发布 > 影像发布服务 > 功能说明\n\n发布上传到服务器端的DOM数据。",
                metadata={
                    "chunk_id": "server_publish",
                    "rrf_score": 0.05,
                    "source": "StampServer用户手册_Rocky9 .docx",
                    "section_path": "3Dtiles数据服务发布 > 影像发布服务 > 功能说明",
                },
            ),
            Document(
                page_content="# TerrainBuilder > DOMBuilder > 发布影像\n\nDOMBuilder 编译完成后可发布影像成果。",
                metadata={
                    "chunk_id": "tool_publish",
                    "rrf_score": 0.035,
                    "source": "StampTools用户手册.docx",
                    "section_path": "TerrainBuilder > DOMBuilder > 发布影像",
                },
            ),
        ]

        result = strategy.apply("DOMBuilder 如何发布影像", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "tool_publish")
        self.assertGreater(result[0].metadata.get("intent_profile_boost", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
