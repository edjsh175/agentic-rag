"""
博客已发布文章同步服务 —— 定时/手动同步文章到知识库
"""
import re
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime

import httpx

from rag_knowledge.repository.vector_store import VectorStore

logger = logging.getLogger(__name__)

_FILENAME_SAFE = re.compile(r"[^一-鿿\w\-]", re.UNICODE)


def _slug(title: str, max_len: int = 40) -> str:
    safe = _FILENAME_SAFE.sub("_", title).strip("_")
    safe = re.sub(r"_+", "_", safe)
    return safe[:max_len].rstrip("_") if safe else "untitled"


def _hash_file(path: Path, buf: int = 65536) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(buf):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning("计算哈希失败 %s: %s", path, e)
        return None


def _parse_front_matter(file_path: Path) -> dict:
    meta = {}
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
            if first.strip() != "---":
                return meta
            for line in f:
                if line.strip() == "---":
                    break
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip()
    except Exception:
        pass
    return meta


def _delete_file_vectors(file_path: Path, data_dir: Path):
    """删除文件在 ChromaDB 和 file_index 中的记录"""
    fhash = _hash_file(file_path)
    if not fhash:
        return
    index_path = data_dir / "file_index.json"
    if not index_path.exists():
        return
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        entry = index.get("files", {}).pop(fhash, None)
        if entry and entry.get("chunk_ids"):
            VectorStore().delete(entry["chunk_ids"])
            logger.info("已删除向量: %s (%d 块)", file_path.name, len(entry["chunk_ids"]))
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("删除向量失败 %s: %s", file_path, e)


# ---- 同步服务 ----

class BlogPostSyncer:
    """已发布文章同步器"""

    def __init__(self, publish_dir: Path, api_url: str, data_dir: Path):
        self.publish_dir = publish_dir
        self.api_url = api_url
        self.data_dir = data_dir

    def sync(self) -> dict:
        """执行一次同步，返回 {new, updated, skipped, deleted}"""
        self.publish_dir.mkdir(parents=True, exist_ok=True)

        # 1. 调用 API（失败返回 None，不碰本地文件）
        articles = self._fetch_articles()
        if articles is None:
            logger.warning("同步取消: API 获取文章列表失败，保留本地文件不变")
            return {"new": 0, "updated": 0, "skipped": 0, "deleted": 0}
        api_ids = {str(a["id"]) for a in articles}

        # 2. 扫描本地文件，按 id 和 title 双索引
        local_map: dict[str, Path] = {}
        local_by_title: dict[str, Path] = {}
        for fp in self.publish_dir.iterdir():
            if not fp.is_file() or fp.suffix.lower() != ".md":
                continue
            meta = _parse_front_matter(fp)
            aid = meta.get("id", "").strip()
            if aid:
                local_map[aid] = fp
            t = meta.get("title", "").strip()
            if t:
                local_by_title[t] = fp

        new = updated = skipped = deleted = 0

        # 3. 处理 API 文章
        for item in articles:
            aid = str(item["id"])
            title = item.get("title", "").strip()
            content_md = item.get("content_md", "")
            fp = local_map.get(aid) or local_by_title.get(title)

            if fp is None:
                self._write_file(aid, title, content_md)
                new += 1
                continue

            meta = _parse_front_matter(fp)
            old_title = meta.get("title", "")
            body = fp.read_text(encoding="utf-8")
            sep = body.find("\n---\n", 3)
            old_body = body[sep + 5:] if sep > 0 else body

            if old_title.strip() == title and old_body.strip() == content_md.strip():
                # 内容一致，但 id 不匹配时更新 front-matter
                if aid != meta.get("id", "").strip():
                    fp.unlink(missing_ok=True)
                    self._write_file(aid, title, content_md)
                skipped += 1
            else:
                _delete_file_vectors(fp, self.data_dir)
                fp.unlink(missing_ok=True)
                self._write_file(aid, title, content_md)
                updated += 1

        # 4. 删除本地残留
        for aid, fp in list(local_map.items()):
            if aid not in api_ids:
                _delete_file_vectors(fp, self.data_dir)
                fp.unlink(missing_ok=True)
                deleted += 1
                logger.info("已删除残留: %s (id=%s)", fp.name, aid)

        logger.info("同步完成: +%d ~%d =%d -%d", new, updated, skipped, deleted)
        return {"new": new, "updated": updated, "skipped": skipped, "deleted": deleted}

    def _fetch_articles(self) -> list[dict] | None:
        """调用 API 返回 [{id, title, content_md}]，失败返回 None"""
        logger.info("获取已发布文章: %s", self.api_url)
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.get(self.api_url, headers={
                    "User-Agent": "RAG-Knowledge-Syncer/1.0",
                })
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("data", "articles", "items", "list"):
                    val = data.get(key)
                    if isinstance(val, list):
                        return val
            logger.warning("API 返回格式异常: %s", type(data))
            return []
        except Exception as e:
            logger.error("获取文章列表失败: %s", e)
            return None

    def _write_file(self, aid: str, title: str, content_md: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        front = (
            "---\n"
            f"title: {title}\n"
            f"id: {aid}\n"
            f"synced_at: {now}\n"
            "---\n\n"
        )
        filename = f"{aid}-{_slug(title)}.md"
        fp = self.publish_dir / filename
        fp.write_text(front + content_md.strip(), encoding="utf-8")
        logger.info("已同步: %s", fp.name)
