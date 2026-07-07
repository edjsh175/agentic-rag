"""
目录扫描服务 —— 定时扫描监视目录，自动发现并处理新文件

工作流程：
  1. 递归遍历监视目录下的所有文件
  2. 计算 SHA-256 哈希，与已有索引对比
  3. 哈希不存在 → 新文件 → 加载 → 向量化 → 存入 Chroma
  4. 哈希已存在且路径相同 → 跳过（去重）
  5. 哈希存在但路径不同 → 更新索引中记录的路径
  6. 索引中存在但文件已删除 → 清理索引
  7. 定时循环执行（默认 30 分钟）
"""
import json
import hashlib
import time
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from rag_knowledge.config import Config
from rag_knowledge.models.document import FileRecord
from rag_knowledge.services.chunk_admin import DOC_CATEGORIES, classify_doc_category
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.index_cleanup import cleanup_indexed_file
from rag_knowledge.services.loader import FileLoader

logger = logging.getLogger(__name__)


def _fmt_size(n: int) -> str:
    """文件大小友好显示"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}TB"


class DirectoryScanner:
    """目录扫描器"""

    _DOC_CATEGORIES = set(DOC_CATEGORIES)

    def __init__(self):
        self._cfg = Config()
        self._store = VectorStore()
        self._loader = FileLoader()
        self._scheduler: BackgroundScheduler | None = None

        self._index_path = self._cfg.data_dir / "file_index.json"
        self._index: dict = self._load_index()

        # MVP: 文档分类映射表（相对路径 → doc_category），上传时写入，扫描时读取
        self._dc_map_path = self._cfg.data_dir / "doc_category_map.json"
        self._dc_map: dict = self._load_dc_map()
        self._rebuild_dc_map: dict[str, str] = {}
        self._rebuild_hash_dc_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def scan(self) -> dict:
        """
        立即执行一次完整扫描

        返回：
          {new_files: int, skipped_files: int, errors: int, details: list}
        """
        watch_dir = self._cfg.watch_dir
        logger.info("开始扫描目录: %s", watch_dir)

        t0 = time.time()
        new = skip = err = 0
        details = []
        files = self._collect_files(watch_dir)
        logger.info("发现 %d 个待检查文件", len(files))

        for fp in files:
            try:
                status = self._process(fp, watch_dir)
                if status == "new":
                    new += 1
                    details.append(f"[新增] {fp.name}")
                elif status == "skipped":
                    skip += 1
                    details.append(f"[跳过] {fp.name}")
                else:
                    err += 1
                    details.append(f"[失败] {fp.name}")
            except Exception as e:
                err += 1
                logger.error("处理异常 %s: %s", fp, e)

        # 清理已删除的文件索引
        cleaned = self._clean_removed(watch_dir)
        for item in cleaned:
            details.append(f"[清理] {item.file_name}")
        self._save_index()
        if new or any(item.should_rebuild_bm25 for item in cleaned):
            BM25Store().rebuild()
            from rag_knowledge.services.query_cache import clear_query_cache
            clear_query_cache()

        elapsed = time.time() - t0
        parts = [f"新增 {new}"]
        if skip: parts.append(f"跳过 {skip}")
        if err: parts.append(f"失败 {err}")
        if cleaned: parts.append(f"清理 {len(cleaned)}")
        logger.info("扫描完成 | %s | %.2fs", " / ".join(parts), elapsed)
        if details:
            for d in details:
                logger.info("  %s", d)
        return {"new_files": new, "skipped_files": skip, "errors": err, "details": details}

    def start(self):
        """启动定时调度"""
        if self._scheduler and self._scheduler.running:
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self.scan, "interval",
            minutes=self._cfg.scan_interval,
            id="rag_scan",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("定时扫描已启动 (间隔: %d 分钟)", self._cfg.scan_interval)

    def stop(self):
        """停止定时调度"""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("定时扫描已停止")

    def reset_index(self, preserve_doc_categories: bool = True):
        """重置文件索引（重建/清空知识库时调用），下次 scan 重新全量索引"""
        if preserve_doc_categories:
            files = self._index.get("files", {})
            self._rebuild_dc_map = {
                entry["file_path"]: entry.get("doc_category", "")
                for entry in files.values()
                if entry.get("file_path") and entry.get("doc_category")
            }
            self._rebuild_hash_dc_map = {
                file_hash: entry.get("doc_category", "")
                for file_hash, entry in files.items()
                if entry.get("doc_category")
            }
        else:
            self._rebuild_dc_map = {}
            self._rebuild_hash_dc_map = {}
        self._index = {"version": 1, "files": {}}
        self._save_index()

    def get_index(self) -> dict:
        """获取文件索引快照"""
        files = self._index.get("files", {})
        return {"total_files": len(files), "files": list(files.values())}

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    def _collect_files(self, directory: Path) -> list[Path]:
        """递归收集所有匹配扩展名的文件"""
        exts = {f".{t}" for t in self._cfg.watch_file_types}
        return [
            p for p in directory.rglob("*")
            if p.is_file()
            and p.suffix.lower() in exts
            and not p.name.startswith("~$")
            and not p.name.startswith(".")
        ]

    def _process(self, file_path: Path, base: Path) -> str:
        """处理单个文件，返回状态: new / skipped / error"""
        fhash = self._hash(file_path)
        if not fhash:
            return "error"

        rel_path = file_path.relative_to(base)
        rel = str(rel_path)
        kb_name = "已发布文章" if rel_path.parts[0] == "已发布文章" else "文章附件"
        record = self._index.get("files", {}).get(fhash)

        # 已存在且路径相同 → 跳过
        if record and record.get("file_path") == rel:
            return "skipped"

        # 已存在但路径不同（文件被移动/重命名）→ 更新路径
        if record:
            record.update(file_path=rel, file_name=file_path.name,
                          last_modified=datetime.fromtimestamp(file_path.stat().st_mtime).isoformat())
            self._save_index()
            return "skipped"

        # 新文件 → 加载 + 向量化 + 建立索引
        category = FileLoader.detect_category(str(file_path))
        if not category:
            return "skipped"

        try:
            chunks, _ = self._loader.load(str(file_path))
        except Exception as e:
            logger.error("加载失败 %s: %s", file_path, e)
            return "error"

        if not chunks:
            logger.warning("文件内容为空，跳过: %s", file_path.name)
            return "skipped"

        # 为每个 chunk 标记所属知识库 + MVP 新增元数据
        kb_path = str(rel_path.parent)
        doc_category = self._resolve_doc_category(rel_path, rel, fhash)
        if doc_category:
            self._rebuild_dc_map.pop(rel, None)
            self._rebuild_hash_dc_map.pop(fhash, None)
        for d in chunks:
            d.metadata["kb_name"] = kb_name
            d.metadata["kb_path"] = kb_path
            d.metadata["doc_category"] = doc_category    # MVP: 文档分类
            d.metadata.setdefault("section_title", "")    # MVP: 章节标题（由 loader 填充）
            d.metadata.setdefault("section_path", "")     # MVP: 章节路径
            d.metadata.setdefault("section_index", 0)     # MVP: 章节序号
            d.metadata.setdefault("chunk_in_section", 0)  # MVP: 章内块序号
            d.metadata["review_status"] = "pending"       # MVP: 新入库默认待审核
            d.metadata["geo_wkt"] = None                  # MVP: 预留空间字段

        t0 = time.time()
        chunk_ids = self._store.add_chunks(chunks)
        elapsed = time.time() - t0
        st = file_path.stat()
        rec = FileRecord(
            file_hash=fhash, file_path=rel, file_name=file_path.name,
            file_size=st.st_size, category=category,
            last_modified=datetime.fromtimestamp(st.st_mtime).isoformat(),
            added_at=datetime.now().isoformat(),
            doc_category=doc_category,
            chunk_ids=chunk_ids,
        )
        self._index.setdefault("files", {})[fhash] = {
            "file_path": rec.file_path, "file_name": rec.file_name,
            "file_size": rec.file_size, "category": rec.category,
            "kb_name": kb_name, "doc_category": doc_category,
            "last_modified": rec.last_modified, "added_at": rec.added_at,
            "chunk_ids": rec.chunk_ids,
        }
        logger.info(
            "新文件: %s (%s) | %d 块 | %.2fs",
            file_path.name, category, len(chunks), elapsed,
        )
        return "new"

    def _clean_removed(self, base: Path) -> list:
        """清理已被删除文件的索引记录，同时删除对应向量。"""
        files = self._index.get("files", {})
        removed_hashes = [h for h, r in files.items() if not (base / r["file_path"]).exists()]
        cleaned = []
        for file_hash in removed_hashes:
            cleaned.append(
                cleanup_indexed_file(
                    file_hash,
                    data_dir=self._cfg.data_dir,
                    index_data=self._index,
                    persist=False,
                )
            )
        return cleaned

    # ------------------------------------------------------------------
    # 哈希 & 持久化
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(path: Path, buf: int = 65536) -> str | None:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(buf):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.error("计算哈希失败 %s: %s", path, e)
            return None

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("索引文件损坏，将重建: %s", e)
        return {"version": 1, "files": {}}

    def _save_index(self):
        try:
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("保存索引失败: %s", e)

    def _load_dc_map(self) -> dict:
        """加载文档分类映射表"""
        if self._dc_map_path.exists():
            try:
                return json.loads(self._dc_map_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_dc_map(self):
        """持久化文档分类映射表"""
        try:
            self._dc_map_path.write_text(
                json.dumps(self._dc_map, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("保存分类映射失败: %s", e)

    def set_doc_category(self, relative_path: str, doc_category: str) -> None:
        """上传文件后调用，为文件指定文档分类（扫描前写入映射表）"""
        self._dc_map[relative_path] = doc_category
        self._save_dc_map()

    def _resolve_doc_category(self, rel_path: Path, rel: str, file_hash: str) -> str:
        """
        解析文档分类，优先级：
          1. /upload 显式传入的 doc_category
          2. rebuild 时从旧 file_index 继承
          3. 根据目录名自动推断
          4. 回退为“其他”
        """
        explicit = self._dc_map.pop(rel, "")
        if explicit:
            self._save_dc_map()
            return explicit

        inherited = self._rebuild_dc_map.get(rel) or self._rebuild_hash_dc_map.get(file_hash, "")
        if inherited:
            return inherited

        return classify_doc_category(rel, rel_path.name, "已发布文章" if "已发布文章" in rel_path.parts else "")
