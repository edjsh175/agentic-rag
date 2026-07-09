from pathlib import Path

import pytest

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store


def reset_singletons() -> None:
    Config._instance = None
    RelationalDB._instance = None
    VectorStore._instance = None
    BM25Store._instance = None


@pytest.fixture(autouse=True)
def _reset_singletons_between_tests():
    reset_singletons()
    yield
    reset_singletons()


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    def apply(
        *,
        db_name: str = "test.db",
        chroma_name: str = "chroma",
        data_dir_name: str = "data",
    ) -> tuple[Config, Path, Path, Path]:
        db_path = tmp_path / db_name
        chroma_dir = tmp_path / chroma_name
        data_dir = tmp_path / data_dir_name
        log_dir = tmp_path / "logs"
        watch_dir = tmp_path / "watch_directory"
        blog_posts_dir = tmp_path / "blog_posts"
        blog_crawl_dir = tmp_path / "scrape_article"
        crawl_image_dir = tmp_path / "scrapingImages"
        data_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("RELATIONAL_DB_DB_PATH", str(db_path))
        monkeypatch.setenv("VECTOR_STORE_PERSIST_DIRECTORY", str(chroma_dir))
        monkeypatch.setenv("PATH_DATA_DIR", str(data_dir))
        monkeypatch.setenv("PATH_LOG_DIR", str(log_dir))
        monkeypatch.setenv("SCANNER_WATCH_DIRECTORY", str(watch_dir))
        monkeypatch.setenv("BLOG_POSTS_DIR", str(blog_posts_dir))
        monkeypatch.setenv("BLOG_CRAWL_DIR", str(blog_crawl_dir))
        monkeypatch.setenv("CRAWL_IMAGE_DIR", str(crawl_image_dir))

        reset_singletons()
        cfg = Config()
        return cfg, db_path, chroma_dir, data_dir

    return apply
