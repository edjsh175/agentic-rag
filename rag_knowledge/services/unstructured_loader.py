"""
基于 unstructured 的章节感知文档加载器

使用 unstructured 解析文档结构（Title / NarrativeText / ListItem 等），
再通过 chunk_by_title 按标题边界组织章节块，每个块保留完整的章节元数据。

支持的格式：.txt / .md / .docx（PDF 因 partition.auto 在 Windows 上不稳定，走旧逻辑）
"""
import logging
from pathlib import Path

from langchain_core.documents import Document
from unstructured.chunking.title import chunk_by_title
from unstructured.partition.docx import partition_docx
from unstructured.partition.md import partition_md
from unstructured.partition.text import partition_text

from rag_knowledge.models.document import FileCategory

logger = logging.getLogger(__name__)

# 后缀 → 对应 unstructured partition 函数
_PARTITIONERS = {
    ".txt": partition_text,
    ".md": partition_md,
    ".docx": partition_docx,
}

SUPPORTED_EXTS = set(_PARTITIONERS.keys())


class UnstructuredChapterLoader:
    """使用 unstructured 解析文档并按标题切片"""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 150, strategy: str = "fast"):
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._strategy = strategy

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def load(self, file_path: str) -> list[Document]:
        """解析文档并按标题切片，返回 LangChain Document 列表"""
        path = Path(file_path)
        suffix = path.suffix.lower()

        partition_fn = _PARTITIONERS.get(suffix)
        if partition_fn is None:
            raise ValueError(f"unstructured 不支持的文件类型: {suffix}")

        logger.info("unstructured 解析: %s (strategy=%s)", path.name, self._strategy)

        elements = partition_fn(filename=str(path), strategy=self._strategy)

        chunks = chunk_by_title(
            elements,
            max_characters=self._chunk_size,
            new_after_n_chars=max(int(self._chunk_size * 0.8), 1),
            overlap=self._chunk_overlap,
            combine_text_under_n_chars=100,
            include_orig_elements=True,
        )

        docs: list[Document] = []
        for i, chunk in enumerate(chunks):
            content = str(chunk).strip()
            if not content:
                continue

            section_title = self._extract_section_title(chunk)
            meta = chunk.metadata.to_dict() if hasattr(chunk.metadata, "to_dict") else {}

            docs.append(Document(
                page_content=content,
                metadata={
                    "source": path.name,
                    "category": FileCategory.TEXT,
                    "section_title": section_title,
                    "section_path": section_title,
                    "section_index": i,
                    "chunk_in_section": 0,
                    "page_number": meta.get("page_number"),
                },
            ))

        logger.info("unstructured 解析完成: %s → %d chunks", path.name, len(docs))
        return docs

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_section_title(chunk) -> str:
        """从 chunk 的原始元素中提取第一个 Title 作为章节标题"""
        orig_elements = getattr(chunk.metadata, "orig_elements", None) if hasattr(chunk, "metadata") else None
        if not orig_elements:
            return ""

        for element in orig_elements:
            if getattr(element, "category", "") == "Title":
                return str(element).strip()

        return ""
