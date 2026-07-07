import unittest

from langchain_core.documents import Document

from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy


class RoutingHeuristicTests(unittest.TestCase):
    def test_manual_queries_route_to_attachment_kb(self):
        chain = object.__new__(RagChain)
        self.assertEqual(chain._route_query("管线点表规范"), "文章附件")
        self.assertEqual(chain._route_query("PipelineBuilder 如何发布管线"), "文章附件")
        self.assertEqual(chain._route_query("管线发布服务如何配置"), "文章附件")
        self.assertTrue(chain._is_table_oriented_query("PipelineBuilder 管线点表字段要求"))

    def test_blog_queries_route_to_published_kb(self):
        chain = object.__new__(RagChain)
        self.assertEqual(chain._route_query("CSDN 博客里有 Rocky9 安装经验吗"), "已发布文章")


class RetrievalStrategyStructuredBoostTests(unittest.TestCase):
    def test_structured_queries_get_table_friendly_suffix(self):
        self.assertEqual(
            RetrievalStrategy._augment_structured_query("管线点表规范"),
            "管线点表规范 字段名 说明 数据结构",
        )
        self.assertEqual(
            RetrievalStrategy._augment_structured_query("管点编号 地面高程 特征 附属物设施"),
            "管点编号 地面高程 特征 附属物设施",
        )

    def test_table_oriented_queries_boost_table_chunks(self):
        strategy = RetrievalStrategy()
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

        result = strategy._apply_structured_query_boost("管线点表规范", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "table")
        self.assertGreater(result[0].metadata.get("structured_query_boost", 0.0), 0.0)

    def test_point_table_query_prefers_point_table_over_other_tables(self):
        strategy = RetrievalStrategy()
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

        result = strategy._apply_structured_query_boost("PipelineBuilder 管线点表字段要求", docs)

        self.assertEqual(result[0].metadata.get("chunk_id"), "point_table")


if __name__ == "__main__":
    unittest.main()
