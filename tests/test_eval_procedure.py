import unittest
from unittest.mock import MagicMock, patch

from rag_knowledge.evaluation.eval_procedure_questions import (
    extract_entities_from_vector_store,
    generate_procedure_eval_questions
)


class EvalProcedureTests(unittest.TestCase):
    def setUp(self):
        # 模拟 VectorStore 返回的元数据
        self.mock_metadata = [
            {"source": "DOMBuilder-QuickStart.docx", "section_title": "DOMBuilder Setup"},
            {"source": "DOMBuilder-QuickStart.docx", "section_title": "1. 工程设置"},
            {"source": "StampTools-UserManual.pdf", "section_title": "StampTools Introduction"},
            {"source": "StampTools-UserManual.pdf", "section_title": "2. Data Import"},
            {"source": "0e57a89c3a3e-MySQL-Setup.md", "section_title": "MySQL Installation"},
            {"source": "empty.txt", "section_title": ""},
            {"source": "a.txt", "section_title": ""} # 过滤太短的文件名
        ]

    @patch('rag_knowledge.repository.vector_store.VectorStore.get_chunk_stats_source')
    def test_extract_entities_from_metadata(self, mock_get_stats):
        mock_get_stats.return_value = {
            "ids": [f"id-{i}" for i in range(len(self.mock_metadata))],
            "documents": ["content" for _ in range(len(self.mock_metadata))],
            "metadatas": self.mock_metadata
        }
        
        entities = extract_entities_from_vector_store()
        
        # 转换为无频次列表以便检查
        entity_names = [e[0] for e in entities]
        
        self.assertIn("DOMBuilder", entity_names)
        self.assertIn("StampTools", entity_names)
        self.assertIn("MySQL", entity_names)
        
        # 应该过滤掉 length < 2 的 'a' 或者 stopwords 中的 'docx', 'pdf'
        self.assertNotIn("a", entity_names)
        self.assertNotIn("docx", entity_names)
        self.assertNotIn("pdf", entity_names)

    @patch('rag_knowledge.evaluation.eval_procedure_questions.extract_entities_from_vector_store')
    def test_generate_questions_correct_intent(self, mock_extract):
        mock_extract.return_value = [("DOMBuilder", 10), ("StampTools", 5)]
        
        questions = generate_procedure_eval_questions(max_entities=2, templates_per_intent=1)
        
        # 每一个 entity 会生成 5 个单实体意图 + 1 个双实体意图 (因为 templates_per_intent=1)
        # 总共 2 个实体，各自 5 个单实体意图 = 10 个问题
        # 对比意图: DOMBuilder 和 StampTools 对比 = 1 个问题； StampTools 和 DOMBuilder 对比 = 1 个问题。
        # 共 12 个问题
        self.assertEqual(len(questions), 12)
        
        # 检查结构
        for q in questions:
            self.assertIn("question", q)
            self.assertIn("expected_intent", q)
            self.assertIn("entity", q)
            self.assertIn("template", q)
            
        # 检查其中一个意图
        proc_q = next(q for q in questions if q["expected_intent"] == "procedure")
        self.assertIn(proc_q["entity"], proc_q["question"])
        self.assertIn("如何使用", proc_q["question"])

    @patch('rag_knowledge.evaluation.eval_procedure_questions.extract_entities_from_vector_store')
    def test_comparison_template_pairs(self, mock_extract):
        mock_extract.return_value = [("DOMBuilder", 10), ("StampTools", 5)]
        
        questions = generate_procedure_eval_questions(max_entities=2, templates_per_intent=1)
        
        comp_qs = [q for q in questions if q["expected_intent"] == "comparison"]
        self.assertTrue(len(comp_qs) >= 2)
        
        for q in comp_qs:
            self.assertIn("entity", q)
            self.assertIn("entity2", q)
            self.assertNotEqual(q["entity"], q["entity2"])


if __name__ == "__main__":
    unittest.main()
