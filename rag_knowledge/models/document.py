"""文档数据模型。"""
from dataclasses import dataclass, field


class FileCategory:
    """文件类型分类。"""
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"


@dataclass
class FileRecord:
    """持久化到文件索引中的记录。"""

    file_hash: str
    file_path: str
    file_name: str
    file_size: int
    category: str
    last_modified: str
    added_at: str
    doc_category: str = ""
    chunk_ids: list = field(default_factory=list)


@dataclass
class ProcessedChunk:
    """加载并切分后的内容及元数据。"""

    content: str
    metadata: dict
