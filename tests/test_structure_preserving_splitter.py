import unittest
import hashlib
import re
from langchain_core.documents import Document
from test_loader_and_dataset import _load_file_loader
from langchain_text_splitters import RecursiveCharacterTextSplitter

def create_loader(chunk_size=120, chunk_overlap=10):
    FileLoader = _load_file_loader()
    loader = object.__new__(FileLoader)
    loader._chunk_size = chunk_size
    loader._chunk_overlap = chunk_overlap
    loader._splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )
    return loader

class StructurePreservingSplitterTests(unittest.TestCase):
    def test_markdown_table_kept_as_single_chunk(self):
        loader = create_loader(chunk_size=200)
        table_text = (
            "| Name | Age | Job |\n"
            "| --- | --- | --- |\n"
            "| Alice | 25 | Dev |\n"
            "| Bob | 30 | Ops |"
        )
        doc = Document(page_content=table_text, metadata={"source": "test.md"})
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].page_content, table_text)
        self.assertEqual(chunks[0].metadata["content_type"], "table")
        self.assertEqual(chunks[0].metadata["row_start"], 1)
        self.assertEqual(chunks[0].metadata["row_end"], 2)
        self.assertTrue(chunks[0].metadata["table_id"].startswith("table_"))

    def test_large_markdown_table_split_by_rows_with_header_repeated(self):
        # Set chunk_size to 90 so it can hold the header (about 42 chars) and only one data row (about 20 chars)
        loader = create_loader(chunk_size=90)
        
        table_text = (
            "| Name | Age | Job |\n"
            "| --- | --- | --- |\n"
            "| Alice | 25 | Dev |\n"
            "| Bob | 30 | Ops |\n"
            "| Charlie | 22 | QA |"
        )
        doc = Document(page_content=table_text, metadata={"source": "test.md"})
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        # Each chunk should repeat the header:
        # "| Name | Age | Job |\n| --- | --- | --- |\n"
        # and contain at least one row.
        self.assertTrue(len(chunks) >= 2)
        
        for idx, chunk in enumerate(chunks):
            self.assertEqual(chunk.metadata["content_type"], "table")
            self.assertTrue(chunk.page_content.startswith("| Name | Age | Job |\n| --- | --- | --- |\n"))
            self.assertIn("row_start", chunk.metadata)
            self.assertIn("row_end", chunk.metadata)
            self.assertTrue(chunk.metadata["row_start"] <= chunk.metadata["row_end"])
            
        # Verify first and last chunk row numbers
        self.assertEqual(chunks[0].metadata["row_start"], 1)
        self.assertEqual(chunks[-1].metadata["row_end"], 3)
        # All chunks share the same table_id
        table_ids = {c.metadata["table_id"] for c in chunks}
        self.assertEqual(len(table_ids), 1)

    def test_excel_markdown_table_preserved(self):
        loader = create_loader(chunk_size=150)
        table_text = (
            "| SheetName |\n"
            "| --- |\n"
            "| Data1 |\n"
            "| Data2 |"
        )
        doc = Document(
            page_content=table_text,
            metadata={
                "source": "test.xlsx",
                "sheet": "Sheet1",
                "content_type": "table",
                "table_source": "excel"
            }
        )
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].page_content, table_text)
        self.assertEqual(chunks[0].metadata["content_type"], "table")
        self.assertEqual(chunks[0].metadata["sheet"], "Sheet1")
        self.assertEqual(chunks[0].metadata["table_source"], "excel")

    def test_fenced_code_block_kept_as_single_chunk(self):
        loader = create_loader(chunk_size=200)
        code_text = (
            "```python\n"
            "def hello():\n"
            "    print('hi')\n"
            "```"
        )
        doc = Document(page_content=code_text, metadata={"source": "test.md"})
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].page_content, code_text)
        self.assertEqual(chunks[0].metadata["content_type"], "code")
        self.assertEqual(chunks[0].metadata["language"], "python")
        self.assertEqual(chunks[0].metadata["part_index"], 1)
        self.assertTrue(chunks[0].metadata["code_block_index"].startswith("code_"))

    def test_large_code_block_split_by_lines_with_fence_preserved(self):
        # Set chunk_size to 35 so each chunk can hold ```python\n + 1 or 2 lines + \n```
        loader = create_loader(chunk_size=35)
        code_text = (
            "```python\n"
            "line1 = 1\n"
            "line2 = 2\n"
            "line3 = 3\n"
            "line4 = 4\n"
            "```"
        )
        doc = Document(page_content=code_text, metadata={"source": "test.md"})
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        self.assertTrue(len(chunks) >= 2)
        for idx, chunk in enumerate(chunks):
            self.assertEqual(chunk.metadata["content_type"], "code")
            self.assertEqual(chunk.metadata["language"], "python")
            self.assertTrue(chunk.page_content.startswith("```python\n"))
            self.assertTrue(chunk.page_content.endswith("\n```"))
            self.assertEqual(chunk.metadata["part_index"], idx + 1)
            
        code_block_indices = {c.metadata["code_block_index"] for c in chunks}
        self.assertEqual(len(code_block_indices), 1)

    def test_plain_text_still_uses_normal_splitter(self):
        loader = create_loader(chunk_size=30)
        plain_text = "This is a very long sentence that will be split by recursive character text splitter."
        doc = Document(page_content=plain_text, metadata={"source": "test.md"})
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        self.assertTrue(len(chunks) > 1)
        # Ensure that no metadata fields like content_type are added to plain text chunks
        for chunk in chunks:
            self.assertNotIn("content_type", chunk.metadata)
            
    def test_mixed_markdown_preserving(self):
        loader = create_loader(chunk_size=150)
        mixed_text = (
            "This is a long introductory paragraph that has enough content to pass the low information check.\n\n"
            "| Col1 |\n"
            "| --- |\n"
            "| Val1 |\n\n"
            "This is a long concluding paragraph that also has enough content to pass the low information check.\n\n"
            "```python\n"
            "x = 1\n"
            "```"
        )
        doc = Document(page_content=mixed_text, metadata={"source": "mixed.md"})
        
        chunks = loader._split_documents_preserving_blocks([doc])
        chunks = loader._post_process_chunks(chunks)
        
        # We expect a text chunk for intro, table chunk, text chunk for outro, code chunk
        types = [c.metadata.get("content_type") for c in chunks]
        self.assertIn("table", types)
        self.assertIn("code", types)
        self.assertIn(None, types)

if __name__ == "__main__":
    unittest.main()
