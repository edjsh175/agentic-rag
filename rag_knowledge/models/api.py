"""API 请求与响应数据模型。"""
from typing import Optional

from pydantic import BaseModel


class HistoryItem(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str
    collection_name: Optional[str] = "rag_knowledge"
    kb_name: Optional[str] = None
    doc_category: Optional[str] = None
    history: Optional[list[HistoryItem]] = None
    llm_model: Optional[str] = None
    vision_model: Optional[str] = None
    thinking: Optional[bool] = None
    web_search: Optional[bool] = None
    allow_general_knowledge: Optional[bool] = None
    agent_prompt: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
    source_documents: list


class UploadResponse(BaseModel):
    message: str
    chunks_count: int
    file_name: str


class ScanResponse(BaseModel):
    message: str
    new_files: int
    skipped_files: int
    errors: int


class StatsResponse(BaseModel):
    total_chunks: int
    collection_name: str
    watched_directory: str
    file_types: list
    scan_interval_minutes: int


class ReviewRequest(BaseModel):
    file_paths: Optional[list[str]] = None
    chunk_ids: Optional[list[str]] = None
    status: str = "approved"


class ReviewResponse(BaseModel):
    message: str
    updated_chunks: int
    requested_chunks: int
    status: str


class CrawlRequest(BaseModel):
    url: str


class CrawlResponse(BaseModel):
    title: str
    source_url: str
    author: str
    platform: str
    publish_date: str | None = None
    file_path: str
    message: str


class BlogPostItem(BaseModel):
    filename: str
    title: str
    author: str | None = None
    platform: str | None = None
    file_path: str
    file_size: int
    crawled_at: str | None = None


class BlogPostListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    posts: list[BlogPostItem]
    posts_dir: str
