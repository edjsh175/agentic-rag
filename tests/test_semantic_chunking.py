import importlib
import sys
import unittest
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


_INJECTED_MODULES = []


def _inject_stub(name, stub):
    global _INJECTED_MODULES
    if name not in sys.modules:
        sys.modules[name] = stub
        _INJECTED_MODULES.append(name)


def _install_loader_stubs():
    unstructured_stub = ModuleType("rag_knowledge.services.unstructured_loader")
    unstructured_stub.UnstructuredChapterLoader = type("UnstructuredChapterLoader", (), {})
    unstructured_stub.SUPPORTED_EXTS = {".txt", ".md", ".docx"}
    _inject_stub("rag_knowledge.services.unstructured_loader", unstructured_stub)

    models_pkg = ModuleType("rag_knowledge.models")
    document_mod = ModuleType("rag_knowledge.models.document")

    class FileCategory:
        TEXT = "text"
        IMAGE = "image"
        VIDEO = "video"

    document_mod.FileCategory = FileCategory
    _inject_stub("rag_knowledge.models", models_pkg)
    _inject_stub("rag_knowledge.models.document", document_mod)

    return FileCategory


def tearDownModule():
    global _INJECTED_MODULES
    for name in _INJECTED_MODULES:
        sys.modules.pop(name, None)
    _INJECTED_MODULES.clear()


def _load_loader_and_chunker():
    _install_loader_stubs()
    sys.modules.pop("rag_knowledge.services.loader", None)
    sys.modules.pop("rag_knowledge.services.semantic_chunker", None)

    loader_module = importlib.import_module("rag_knowledge.services.loader")
    chunker_module = importlib.import_module("rag_knowledge.services.semantic_chunker")
    return loader_module.FileLoader, chunker_module.SemanticChunker


class FakeEmbeddings:
    def __init__(self, vectors=None, error=None):
        self._vectors = vectors or {}
        self._error = error
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        return [self._vectors[text] for text in texts]


class StaticEmbeddings:
    def __init__(self, response):
        self._response = response

    def embed_documents(self, texts):
        return self._response


def _build_loader(chunk_size=120, chunk_overlap=10, semantic_enabled=True, embeddings=None):
    FileLoader, SemanticChunker = _load_loader_and_chunker()
    loader = object.__new__(FileLoader)
    loader._chunk_size = chunk_size
    loader._chunk_overlap = chunk_overlap
    loader._semantic_chunking_enabled = semantic_enabled
    loader._splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", ";", " ", ""],
    )
    loader._semantic_chunker = SemanticChunker(
        embeddings=embeddings,
        fallback_splitter=loader._splitter,
        max_chunk_size=chunk_size,
        min_chunk_size=40,
        breakpoint_percentile=80,
    )
    return loader, SemanticChunker


class SemanticChunkerAlgorithmTests(unittest.TestCase):
    def test_semantic_boundary_splits_at_topic_shift(self):
        _, SemanticChunker = _load_loader_and_chunker()
        splitter = RecursiveCharacterTextSplitter(chunk_size=240, chunk_overlap=20)
        text = "\n\n".join([
            "Alpha topic sentence one. Alpha topic sentence two.",
            "Alpha continuation with nearby wording.",
            "Beta topic begins here with very different meaning.",
            "Beta topic continues with related details.",
        ])
        doc = Document(page_content=text, metadata={"source": "semantic.md"})
        embeddings = FakeEmbeddings(vectors={
            "Alpha topic sentence one.": [1.0, 0.0],
            "Alpha topic sentence two.": [0.99, 0.01],
            "Alpha continuation with nearby wording.": [0.98, 0.02],
            "Beta topic begins here with very different meaning.": [0.0, 1.0],
            "Beta topic continues with related details.": [0.01, 0.99],
        })
        chunker = SemanticChunker(
            embeddings=embeddings,
            fallback_splitter=splitter,
            max_chunk_size=240,
            min_chunk_size=40,
            breakpoint_percentile=80,
        )

        chunks = chunker.split_document(doc)

        self.assertEqual(len(chunks), 2)
        self.assertIn("Alpha continuation", chunks[0].page_content)
        self.assertIn("Beta topic begins", chunks[1].page_content)
        self.assertTrue(all(chunk.metadata["chunking_method"] == "semantic" for chunk in chunks))

    def test_max_chunk_size_forces_split_without_semantic_boundary(self):
        _, SemanticChunker = _load_loader_and_chunker()
        splitter = RecursiveCharacterTextSplitter(chunk_size=90, chunk_overlap=10)
        units = [
            "A" * 40 + ".",
            "B" * 40 + ".",
            "C" * 40 + ".",
        ]
        doc = Document(page_content="\n\n".join(units), metadata={"source": "semantic.md"})
        embeddings = FakeEmbeddings(vectors={unit: [1.0, 0.0] for unit in units})
        chunker = SemanticChunker(
            embeddings=embeddings,
            fallback_splitter=splitter,
            max_chunk_size=90,
            min_chunk_size=20,
            breakpoint_percentile=80,
        )

        chunks = chunker.split_document(doc)

        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks[0].page_content), 90)
        self.assertLessEqual(len(chunks[1].page_content), 90)

    def test_short_paragraph_is_split_into_sentence_units(self):
        _, SemanticChunker = _load_loader_and_chunker()
        splitter = RecursiveCharacterTextSplitter(chunk_size=240, chunk_overlap=20)
        sentences = [
            "Alpha topic starts with enough detail.",
            "Alpha topic continues with related detail.",
            "Beta topic starts with different detail.",
            "Beta topic continues with related detail.",
        ]
        embeddings = FakeEmbeddings(vectors={
            sentences[0]: [1.0, 0.0],
            sentences[1]: [0.99, 0.01],
            sentences[2]: [0.0, 1.0],
            sentences[3]: [0.01, 0.99],
        })
        chunker = SemanticChunker(
            embeddings=embeddings,
            fallback_splitter=splitter,
            max_chunk_size=240,
            min_chunk_size=50,
            breakpoint_percentile=80,
        )

        chunks = chunker.split_document(Document(page_content=" ".join(sentences), metadata={}))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(embeddings.calls, [sentences])

    def test_two_different_units_can_form_a_semantic_boundary(self):
        _, SemanticChunker = _load_loader_and_chunker()
        splitter = RecursiveCharacterTextSplitter(chunk_size=240, chunk_overlap=20)
        units = ["Alpha topic has sufficient detail here.", "Beta topic has very different detail here."]
        chunker = SemanticChunker(
            embeddings=FakeEmbeddings(vectors={
                units[0]: [1.0, 0.0],
                units[1]: [0.0, 1.0],
            }),
            fallback_splitter=splitter,
            max_chunk_size=240,
            min_chunk_size=30,
            breakpoint_percentile=80,
        )

        chunks = chunker.split_document(Document(page_content="\n\n".join(units), metadata={}))

        self.assertEqual([chunk.page_content for chunk in chunks], units)

    def test_small_trailing_unit_is_merged_instead_of_cut_at_semantic_edge(self):
        _, SemanticChunker = _load_loader_and_chunker()
        splitter = RecursiveCharacterTextSplitter(chunk_size=240, chunk_overlap=20)
        units = ["A" * 65 + ".", "B" * 65 + ".", "tiny tail."]
        embeddings = FakeEmbeddings(vectors={
            units[0]: [1.0, 0.0],
            units[1]: [0.0, 1.0],
            units[2]: [0.0, 1.0],
        })
        chunker = SemanticChunker(
            embeddings=embeddings,
            fallback_splitter=splitter,
            max_chunk_size=240,
            min_chunk_size=50,
            breakpoint_percentile=80,
        )

        chunks = chunker.split_document(Document(page_content="\n\n".join(units), metadata={}))

        self.assertEqual(len(chunks), 2)
        self.assertIn("tiny tail", chunks[-1].page_content)
        self.assertGreaterEqual(len(chunks[-1].page_content), 50)


class LoaderSemanticChunkingTests(unittest.TestCase):
    def test_semantic_mode_disables_unstructured_sliding_overlap(self):
        FileLoader, _ = _load_loader_and_chunker()
        loader_module = sys.modules[FileLoader.__module__]
        config = SimpleNamespace(
            chunk_size=160,
            chunk_overlap=25,
            semantic_chunking_enabled=True,
            semantic_breakpoint_percentile=80,
            semantic_min_chunk_size=40,
            ollama_base_url="http://localhost:11434",
            embedding_model="fake-embedding",
            vision_model="fake-vision",
            extract_embedded_images=False,
            use_unstructured=True,
            unstructured_strategy="fast",
            vision_endpoint=None,
            embedding_endpoint=None,
        )

        with (
            patch.object(loader_module, "Config", return_value=config),
            patch.object(loader_module, "OllamaEmbeddings", return_value=FakeEmbeddings()),
            patch.object(loader_module, "UnstructuredChapterLoader") as chapter_loader,
        ):
            FileLoader()

        chapter_loader.assert_called_once_with(
            chunk_size=160,
            chunk_overlap=0,
            strategy="fast",
        )

    def test_embedding_client_initialization_failure_does_not_break_loader(self):
        FileLoader, _ = _load_loader_and_chunker()
        loader_module = sys.modules[FileLoader.__module__]
        config = SimpleNamespace(
            chunk_size=40,
            chunk_overlap=5,
            semantic_chunking_enabled=True,
            semantic_breakpoint_percentile=80,
            semantic_min_chunk_size=20,
            ollama_base_url="invalid-url",
            embedding_model="missing-model",
            vision_model="fake-vision",
            extract_embedded_images=False,
            use_unstructured=False,
            unstructured_strategy="fast",
            vision_endpoint=None,
            embedding_endpoint=None,
        )

        with (
            patch.object(loader_module, "Config", return_value=config),
            patch.object(loader_module, "OllamaEmbeddings", side_effect=ValueError("bad client config")),
        ):
            loader = FileLoader()

        chunks = loader._split_plain_text_block(Document(
            page_content="Plain text remains ingestible when the embedding client cannot initialize.",
            metadata={"source": "plain.md"},
        ))
        self.assertIsNone(loader._semantic_chunker)
        self.assertTrue(all(chunk.metadata["chunking_method"] == "fixed_fallback" for chunk in chunks))

    def test_loader_uses_semantic_for_plain_text_and_keeps_table_and_code_protected(self):
        embeddings = FakeEmbeddings(vectors={
            "Alpha text explains one topic in enough detail for semantic chunking.": [1.0, 0.0],
            "Alpha follow-up stays on the same topic and should merge.": [0.99, 0.01],
            "Beta text starts a new topic and should become a new semantic chunk.": [0.0, 1.0],
        })
        loader, _ = _build_loader(chunk_size=160, embeddings=embeddings)
        doc = Document(
            page_content=(
                "Alpha text explains one topic in enough detail for semantic chunking.\n\n"
                "Alpha follow-up stays on the same topic and should merge.\n\n"
                "| Col |\n| --- |\n| Val |\n\n"
                "Beta text starts a new topic and should become a new semantic chunk.\n\n"
                "```python\nprint('ok')\n```"
            ),
            metadata={"source": "mixed.md"},
        )

        chunks = loader._split_documents_preserving_blocks([doc])

        methods = [chunk.metadata.get("chunking_method") for chunk in chunks]
        self.assertIn("semantic", methods)
        self.assertIn("table", methods)
        self.assertIn("code", methods)
        self.assertEqual(len(embeddings.calls), 2)
        embedded_text = "\n".join("\n".join(batch) for batch in embeddings.calls)
        self.assertNotIn("| Col |", embedded_text)
        self.assertNotIn("```python", embedded_text)

    def test_loader_falls_back_when_embeddings_fail(self):
        loader, _ = _build_loader(
            chunk_size=45,
            chunk_overlap=5,
            embeddings=FakeEmbeddings(error=TimeoutError("embedding timeout")),
        )
        doc = Document(
            page_content=(
                "This is a long plain text paragraph that should split with the recursive fallback "
                "when semantic embeddings are unavailable."
            ),
            metadata={"source": "plain.md"},
        )

        chunks = loader._split_documents_preserving_blocks([doc])

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.metadata["chunking_method"] == "fixed_fallback" for chunk in chunks))

    def test_loader_can_disable_semantic_chunking(self):
        embeddings = FakeEmbeddings(vectors={
            "This text would otherwise be embedded.": [1.0, 0.0],
        })
        loader, _ = _build_loader(
            chunk_size=30,
            chunk_overlap=5,
            semantic_enabled=False,
            embeddings=embeddings,
        )
        doc = Document(
            page_content="This text would otherwise be embedded. This extra sentence forces fallback splitting.",
            metadata={"source": "plain.md"},
        )

        chunks = loader._split_documents_preserving_blocks([doc])

        self.assertGreater(len(chunks), 1)
        self.assertEqual(embeddings.calls, [])
        self.assertTrue(all(chunk.metadata["chunking_method"] == "fixed_fallback" for chunk in chunks))

    def test_invalid_embedding_responses_fall_back_for_whole_plain_text_block(self):
        invalid_responses = [
            [],
            [[1.0, 0.0]],
            [[1.0, 0.0], [1.0]],
            [[0.0, 0.0], [1.0, 0.0]],
        ]
        text = "First sentence has enough text to split. Second sentence also has enough text to split."

        for response in invalid_responses:
            with self.subTest(response=response):
                loader, _ = _build_loader(chunk_size=60, embeddings=StaticEmbeddings(response))
                chunks = loader._split_documents_preserving_blocks([
                    Document(page_content=text, metadata={"source": "invalid.md"})
                ])

                self.assertGreater(len(chunks), 1)
                self.assertTrue(all(
                    chunk.metadata["chunking_method"] == "fixed_fallback"
                    for chunk in chunks
                ))

    def test_separate_heading_sections_are_never_merged(self):
        loader, _ = _build_loader(
            chunk_size=240,
            embeddings=FakeEmbeddings(vectors={
                "First section sentence one.": [1.0, 0.0],
                "First section sentence two.": [1.0, 0.0],
                "Second section sentence one.": [1.0, 0.0],
                "Second section sentence two.": [1.0, 0.0],
            }),
        )
        docs = [
            Document(
                page_content="First section sentence one. First section sentence two.",
                metadata={"section_title": "First"},
            ),
            Document(
                page_content="Second section sentence one. Second section sentence two.",
                metadata={"section_title": "Second"},
            ),
        ]

        chunks = loader._split_documents_preserving_blocks(docs)

        self.assertEqual({chunk.metadata["section_title"] for chunk in chunks}, {"First", "Second"})
        self.assertFalse(any(
            "First section" in chunk.page_content and "Second section" in chunk.page_content
            for chunk in chunks
        ))


if __name__ == "__main__":
    unittest.main()
