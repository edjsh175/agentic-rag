import re

import numpy as np
from langchain_core.documents import Document


class SemanticChunkingError(RuntimeError):
    """Raised when embedding-backed semantic chunking cannot proceed safely."""


class SemanticChunker:
    def __init__(
        self,
        embeddings,
        fallback_splitter,
        max_chunk_size: int,
        min_chunk_size: int = 200,
        breakpoint_percentile: int = 80,
    ):
        self._embeddings = embeddings
        self._fallback_splitter = fallback_splitter
        self._max_chunk_size = max_chunk_size
        self._min_chunk_size = min_chunk_size
        self._breakpoint_percentile = breakpoint_percentile

    def split_document(self, doc: Document) -> list[Document]:
        units = self._build_units(doc.page_content)
        if not units:
            return []

        chunks: list[Document] = []
        pending_units: list[str] = []
        for unit in units:
            if len(unit) > self._max_chunk_size:
                if pending_units:
                    chunks.extend(self._split_unit_run(pending_units, doc.metadata))
                    pending_units = []
                chunks.extend(self._fallback_document(unit, doc.metadata))
                continue
            pending_units.append(unit)

        if pending_units:
            chunks.extend(self._split_unit_run(pending_units, doc.metadata))

        return chunks

    def _split_unit_run(self, units: list[str], metadata: dict) -> list[Document]:
        vectors = self._embed_units(units)
        if len(units) == 1:
            return [Document(
                page_content=units[0],
                metadata={**metadata, "chunking_method": "semantic"},
            )]

        distances = self._adjacent_distances(vectors)
        threshold = float(np.percentile(distances, self._breakpoint_percentile)) if distances else None
        semantic_edges = {
            index for index, distance in enumerate(distances)
            if threshold is not None and distance >= threshold and distance > 1e-12
        }

        chunks: list[Document] = []
        current_units: list[str] = []
        for index, unit in enumerate(units):
            if current_units and self._joined_length(current_units + [unit]) > self._max_chunk_size:
                chunks.append(Document(
                    page_content=self._join_units(current_units),
                    metadata={**metadata, "chunking_method": "semantic"},
                ))
                current_units = [unit]
                continue

            current_units.append(unit)
            remaining_units = units[index + 1:]
            if (
                index in semantic_edges
                and self._joined_length(current_units) >= self._min_chunk_size
                and self._joined_length(remaining_units) >= self._min_chunk_size
            ):
                chunks.append(Document(
                    page_content=self._join_units(current_units),
                    metadata={**metadata, "chunking_method": "semantic"},
                ))
                current_units = []

        if current_units:
            chunks.append(Document(
                page_content=self._join_units(current_units),
                metadata={**metadata, "chunking_method": "semantic"},
            ))

        return chunks

    def _fallback_document(self, text: str, metadata: dict) -> list[Document]:
        docs = self._fallback_splitter.split_documents([Document(page_content=text, metadata=metadata.copy())])
        for chunk in docs:
            chunk.metadata["chunking_method"] = "fixed_fallback"
        return docs

    def _build_units(self, text: str) -> list[str]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
        if not paragraphs:
            return []

        units: list[str] = []
        for paragraph in paragraphs:
            sentences = self._split_sentences(paragraph)
            if sentences:
                units.extend(sentences)
            else:
                units.append(paragraph)
        return units

    @staticmethod
    def _split_sentences(paragraph: str) -> list[str]:
        fragments = re.split(r"(?<=[。！？!?；;])|(?<=[.?!;])\s+", paragraph)
        return [fragment.strip() for fragment in fragments if fragment and fragment.strip()]

    def _embed_units(self, units: list[str]) -> list[np.ndarray]:
        if self._embeddings is None or not hasattr(self._embeddings, "embed_documents"):
            raise SemanticChunkingError("embedding model is unavailable")

        # 与 VectorStore.add_chunks 对齐：远端 Ollama 超大批量 embed 会失败。
        batch_size = 24
        raw_vectors: list = []
        try:
            for start in range(0, len(units), batch_size):
                batch = units[start : start + batch_size]
                raw_vectors.extend(self._embeddings.embed_documents(batch))
        except SemanticChunkingError:
            raise
        except Exception as exc:
            raise SemanticChunkingError(f"embedding request failed: {exc}") from exc

        if len(raw_vectors) != len(units):
            raise SemanticChunkingError("embedding response count mismatch")

        vectors: list[np.ndarray] = []
        expected_dim = None
        for vector in raw_vectors:
            array = np.asarray(vector, dtype=float)
            if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all():
                raise SemanticChunkingError("embedding response contained invalid vector")
            if expected_dim is None:
                expected_dim = array.size
            elif array.size != expected_dim:
                raise SemanticChunkingError("embedding dimension mismatch")
            if np.linalg.norm(array) == 0:
                raise SemanticChunkingError("embedding vector norm is zero")
            vectors.append(array)
        return vectors

    @staticmethod
    def _adjacent_distances(vectors: list[np.ndarray]) -> list[float]:
        distances: list[float] = []
        for left, right in zip(vectors, vectors[1:]):
            cosine_similarity = float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))
            distances.append(max(0.0, min(2.0, 1 - cosine_similarity)))
        return distances

    @staticmethod
    def _join_units(units: list[str]) -> str:
        return "\n\n".join(unit.strip() for unit in units if unit.strip())

    def _joined_length(self, units: list[str]) -> int:
        return len(self._join_units(units))
