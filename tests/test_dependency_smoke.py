import unittest

class DependencySmokeTests(unittest.TestCase):
    def test_runtime_dependencies_importable(self):
        """Verify that core dependencies are correctly installed and importable."""
        # 1. Word segmentation & BM25
        import jieba
        from rank_bm25 import BM25Okapi
        
        # 2. Embedding / Reranking
        import FlagEmbedding
        
        # 3. Document parsers (including PDF image extractor)
        import fitz  # PyMuPDF
        import unstructured
        
        # Simple sanity assertion to verify they are loaded
        self.assertIsNotNone(jieba)
        self.assertIsNotNone(BM25Okapi)
        self.assertIsNotNone(FlagEmbedding)
        self.assertIsNotNone(fitz)
        self.assertIsNotNone(unstructured)

    def test_fitz_pdf_creation(self):
        """Verify that PyMuPDF (fitz) can create and read a document."""
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Hello from PyMuPDF")
        pdf_bytes = doc.write()
        doc.close()

        # Verify we can re-open and extract text
        doc2 = fitz.open(stream=pdf_bytes, filetype="pdf")
        self.assertEqual(len(doc2), 1)
        self.assertIn("Hello from PyMuPDF", doc2[0].get_text())
        doc2.close()
