from pathlib import Path

import pytest

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.query_planner import QueryPlanner


def test_isolated_storage_overrides_live_paths(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage(
        db_name="isolated.db",
        chroma_name="isolated-chroma",
        data_dir_name="isolated-data",
    )

    db = RelationalDB()
    store = VectorStore()
    root = Path.cwd()

    assert Path(db._db_path) == db_path
    assert Path(store._persist_dir) == chroma_dir
    assert cfg.data_dir == data_dir
    assert cfg.log_dir == data_dir.parent / "logs"
    assert cfg.watch_dir == data_dir.parent / "watch_directory"
    assert cfg.blog_posts_dir == data_dir.parent / "blog_posts"
    assert cfg.blog_crawl_dir == data_dir.parent / "scrape_article"
    assert cfg.crawl_image_dir == data_dir.parent / "scrapingImages"
    assert cfg.blog_publish_dir == cfg.watch_dir / "已发布文章"

    assert Path(db._db_path) != root / "data" / "rag_relational.db"
    assert Path(store._persist_dir) != root / "chroma_db"
    assert cfg.data_dir != root / "data"
    assert cfg.log_dir != root / "logs"
    assert cfg.watch_dir != root / "watch_directory"
    assert cfg.blog_posts_dir != root / "blog_posts"
    assert cfg.blog_crawl_dir != root / "scrape_article"
    assert cfg.crawl_image_dir != root / "scrapingImages"


def test_config_rejects_live_paths_under_pytest_without_opt_in(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_STORAGE_IN_TESTS", raising=False)
    monkeypatch.delenv("RELATIONAL_DB_DB_PATH", raising=False)
    monkeypatch.delenv("VECTOR_STORE_PERSIST_DIRECTORY", raising=False)
    monkeypatch.delenv("PATH_DATA_DIR", raising=False)
    monkeypatch.delenv("PATH_LOG_DIR", raising=False)
    monkeypatch.delenv("SCANNER_WATCH_DIRECTORY", raising=False)
    monkeypatch.delenv("BLOG_POSTS_DIR", raising=False)
    monkeypatch.delenv("BLOG_CRAWL_DIR", raising=False)
    monkeypatch.delenv("CRAWL_IMAGE_DIR", raising=False)
    Config._instance = None

    with pytest.raises(RuntimeError, match="live storage path"):
        Config()


def test_config_allows_live_paths_under_pytest_with_opt_in(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")
    monkeypatch.delenv("RELATIONAL_DB_DB_PATH", raising=False)
    monkeypatch.delenv("VECTOR_STORE_PERSIST_DIRECTORY", raising=False)
    monkeypatch.delenv("PATH_DATA_DIR", raising=False)
    monkeypatch.delenv("PATH_LOG_DIR", raising=False)
    monkeypatch.delenv("SCANNER_WATCH_DIRECTORY", raising=False)
    monkeypatch.delenv("BLOG_POSTS_DIR", raising=False)
    monkeypatch.delenv("BLOG_CRAWL_DIR", raising=False)
    monkeypatch.delenv("CRAWL_IMAGE_DIR", raising=False)
    Config._instance = None

    cfg = Config()

    assert cfg.chroma_dir == Path.cwd() / "chroma_db"
    assert cfg.relational_db_path == Path.cwd() / "data" / "rag_relational.db"


def test_query_planner_can_initialize_after_isolated_storage(isolated_storage):
    cfg, db_path, chroma_dir, _ = isolated_storage(
        db_name="planner-isolation.db",
        chroma_name="planner-isolation-chroma",
        data_dir_name="planner-isolation-data",
    )

    planner = QueryPlanner()

    assert planner._cfg.chroma_dir == chroma_dir
    assert planner._cfg.relational_db_path == db_path
    assert planner._cfg.data_dir == cfg.data_dir
