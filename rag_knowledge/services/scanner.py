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
import logging
import os
import time
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
from rag_knowledge.services.document_profiles import normalize_document_profile

from rag_knowledge.services.document_support import (
    IngestionDecisionStore,
    classify_suffix,
    make_decision,
)

logger = logging.getLogger(__name__)

_INDEX_VERSION = 3
_TRUSTED_PROFILE_SOURCES = {"explicit", "inherited", "profile_map", "default"}


def _normalize_profile_path(value: str) -> str:
    return str(value or "").replace("\\", "/")


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

    def __init__(
        self,
        *,
        cfg=None,
        store=None,
        loader=None,
        index_path: Path | None = None,
        decision_path: Path | None = None,
        refresh_retrieval: bool = True,
        new_chunk_review_status: str = "pending",
    ):
        if new_chunk_review_status not in {"pending", "approved"}:
            raise ValueError("new_chunk_review_status must be pending or approved")
        self._cfg = cfg or Config()
        self._store = store or VectorStore()
        self._loader = loader or FileLoader()
        self._scheduler: BackgroundScheduler | None = None
        self._index_path = index_path or (self._cfg.data_dir / "file_index.json")
        self._refresh_retrieval = refresh_retrieval
        self._new_chunk_review_status = new_chunk_review_status
        self._index: dict = self._load_index()

        # Initialize IngestionDecisionStore
        if decision_path is None:
            if index_path is not None:
                self._decision_path = index_path.parent / "ingestion_decisions.json"
            else:
                self._decision_path = self._cfg.data_dir / "ingestion_decisions.json"
        else:
            self._decision_path = Path(decision_path)
        self._decision_store = IngestionDecisionStore(self._decision_path)

        # MVP: 文档分类映射表（相对路径 → doc_category），上传时写入，扫描时读取
        self._dc_map_path = self._cfg.data_dir / "doc_category_map.json"
        self._dc_map: dict = self._load_dc_map()
        self._rebuild_dc_map: dict[str, str] = {}
        self._rebuild_hash_dc_map: dict[str, str] = {}
        self._profile_map_path = self._cfg.data_dir / "document_profile_map.json"
        self._profile_selection_map_path = self._cfg.data_dir / "document_profile_selection.json"
        self._profile_map: dict = {
            _normalize_profile_path(key): value
            for key, value in self._load_json_map(self._profile_map_path).items()
        }
        self._profile_selection_map: dict = {
            _normalize_profile_path(key): value
            for key, value in self._load_json_map(self._profile_selection_map_path).items()
        }
        self._rebuild_profile_map: dict[str, str] = {}
        self._rebuild_hash_profile_map: dict[str, str] = {}

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
        if self._refresh_retrieval and (
            new or any(item.should_rebuild_bm25 for item in cleaned)
        ):
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
            self._rebuild_profile_map = {
                _normalize_profile_path(entry["file_path"]): entry["document_profile"]
                for entry in files.values()
                if entry.get("file_path")
                and entry.get("document_profile")
                and entry.get("document_profile_source") in _TRUSTED_PROFILE_SOURCES
            }
            self._rebuild_hash_profile_map = {
                file_hash: entry["document_profile"]
                for file_hash, entry in files.items()
                if entry.get("document_profile")
                and entry.get("document_profile_source") in _TRUSTED_PROFILE_SOURCES
            }
        else:
            self._rebuild_dc_map = {}
            self._rebuild_hash_dc_map = {}
            self._rebuild_profile_map = {}
            self._rebuild_hash_profile_map = {}
        self._index = {"version": _INDEX_VERSION, "files": {}}
        self._save_index()
        self._decision_store.reset()

    def reload_index(self) -> None:
        self._index = self._load_index()
        self._decision_store.reload()

    def get_index(self) -> dict:
        """获取文件索引快照"""
        files = self._index.get("files", {})
        return {"total_files": len(files), "files": list(files.values())}

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    def _collect_files(self, directory: Path) -> list[Path]:
        """递归收集监视目录下的所有正常文件（跳过 ~$ 临时文件和以 . 开头的文件）"""
        return [
            p for p in directory.rglob("*")
            if p.is_file()
            and not p.name.startswith("~$")
            and not p.name.startswith(".")
        ]

    def scan(self) -> dict:
        """
        立即执行一次完整扫描
        """
        watch_dir = self._cfg.watch_dir
        logger.info("开始扫描目录: %s", watch_dir)

        t0 = time.time()
        new = skip = queued_cnt = excluded_cnt = err = 0
        details = []

        files = self._collect_files(watch_dir)
        logger.info("发现 %d 个待检查文件", len(files))

        for fp in files:
            try:
                fhash = self._hash(fp)
                if not fhash:
                    err += 1
                    details.append({
                        "status": "error",
                        "path": str(fp),
                        "reason_code": "HASH_FAILED",
                        "locator": None,
                        "message": f"计算哈希失败: {fp.name}",
                    })
                    continue

                rel_path = fp.relative_to(watch_dir)
                rel = str(rel_path)

                enabled_exts = {f".{ext.lower()}" for ext in self._cfg.watch_file_types}
                disp = classify_suffix(fp.suffix, enabled_extensions=enabled_exts)

                if disp.action == "excluded":
                    excluded_cnt += 1
                    dec = make_decision(fp, status="excluded", reason_code=disp.reason_code, file_hash=fhash)
                    if getattr(self, "_decision_store", None) is not None:
                        self._decision_store.replace_for_file(file_path=rel, file_hash=fhash, decisions=[dec])
                    details.append({
                        "status": "excluded",
                        "path": rel,
                        "reason_code": disp.reason_code,
                        "locator": None,
                        "message": dec.message,
                    })
                    continue

                if disp.action == "queued":
                    queued_cnt += 1
                    dec = make_decision(fp, status="queued", reason_code=disp.reason_code, file_hash=fhash)
                    if getattr(self, "_decision_store", None) is not None:
                        self._decision_store.replace_for_file(file_path=rel, file_hash=fhash, decisions=[dec])
                    details.append({
                        "status": "queued",
                        "path": rel,
                        "reason_code": disp.reason_code,
                        "locator": None,
                        "message": dec.message,
                    })
                    continue

                # Process action: "process"
                record = self._index.get("files", {}).get(fhash)

                if record and record.get("file_path") == rel:
                    skip += 1
                    continue

                if record:
                    excluded_cnt += 1
                    dec = make_decision(
                        fp,
                        status="excluded",
                        reason_code="DUPLICATE_CONTENT",
                        file_hash=fhash,
                        message=f"内容与 {record.get('file_path', '')} 重复",
                    )
                    self._decision_store.replace_for_file(
                        file_path=rel,
                        file_hash=fhash,
                        decisions=[dec],
                    )
                    details.append({
                        "status": "excluded",
                        "path": rel,
                        "reason_code": "DUPLICATE_CONTENT",
                        "locator": None,
                        "message": dec.message,
                    })
                    skip += 1
                    continue

                category = FileLoader.detect_category(str(fp))
                if not category:
                    skip += 1
                    continue

                document_profile, document_profile_source = self._resolve_document_profile_with_source(rel, fhash)
                try:
                    load_res = self._loader.load_with_decisions(str(fp), document_profile=document_profile)
                except Exception as e:
                    logger.error("加载解析失败 %s: %s", fp, e)
                    dec = make_decision(fp, status="queued", reason_code="FORMAT_PARSE_FAILED", file_hash=fhash, message=str(e))
                    if getattr(self, "_decision_store", None) is not None:
                        self._decision_store.replace_for_file(file_path=rel, file_hash=fhash, decisions=[dec])
                    queued_cnt += 1
                    details.append({
                        "status": "queued",
                        "path": rel,
                        "reason_code": "FORMAT_PARSE_FAILED",
                        "locator": None,
                        "message": str(e),
                    })
                    continue

                if load_res.decisions:
                    if getattr(self, "_decision_store", None) is not None:
                        self._decision_store.replace_for_file(file_path=rel, file_hash=fhash, decisions=load_res.decisions)
                    has_queued = any(d.status == "queued" for d in load_res.decisions)
                    if has_queued:
                        queued_cnt += 1
                    for d in load_res.decisions:
                        details.append({
                            "status": d.status,
                            "path": rel,
                            "reason_code": d.reason_code,
                            "locator": d.locator,
                            "message": d.message,
                        })
                else:
                    if getattr(self, "_decision_store", None) is not None:
                        self._decision_store.replace_for_file(file_path=rel, file_hash=fhash, decisions=[])

                if not load_res.chunks:
                    logger.warning("文件内容为空，不生成 Chunk: %s", fp.name)
                    if not load_res.decisions:
                        skip += 1
                    continue

                kb_name = "已发布文章" if rel_path.parts[0] == "已发布文章" else "文章附件"
                kb_path = str(rel_path.parent)
                doc_category = self._resolve_doc_category(rel_path, rel, fhash)
                from rag_knowledge.services.backbone_guard import infer_document_entity
                document_entity = infer_document_entity(fp.name, rel)
                if doc_category:
                    self._rebuild_dc_map.pop(rel, None)
                    self._rebuild_hash_dc_map.pop(fhash, None)

                for d in load_res.chunks:
                    d.metadata["kb_name"] = kb_name
                    d.metadata["kb_path"] = kb_path
                    d.metadata["doc_category"] = doc_category
                    d.metadata["document_entity"] = document_entity
                    d.metadata["document_profile"] = document_profile
                    d.metadata["document_profile_source"] = document_profile_source
                    d.metadata.setdefault("section_title", "")
                    d.metadata.setdefault("section_path", "")
                    d.metadata.setdefault("section_index", 0)
                    d.metadata.setdefault("chunk_in_section", 0)
                    d.metadata["review_status"] = self._new_chunk_review_status
                    d.metadata["geo_wkt"] = None

                t_store_0 = time.time()
                try:
                    chunk_ids = self._store.add_chunks(load_res.chunks)
                except Exception as e:
                    err += 1
                    logger.error("向量库写入失败 %s: %s", fp, e)
                    details.append({
                        "status": "error",
                        "path": rel,
                        "reason_code": "VECTOR_STORE_WRITE_FAILED",
                        "locator": None,
                        "message": f"向量库写入失败: {e}",
                    })
                    continue

                elapsed_store = time.time() - t_store_0
                st = fp.stat()
                rec = FileRecord(
                    file_hash=fhash, file_path=rel, file_name=fp.name,
                    file_size=st.st_size, category=load_res.category,
                    last_modified=datetime.fromtimestamp(st.st_mtime).isoformat(),
                    added_at=datetime.now().isoformat(),
                    doc_category=doc_category,
                    document_profile=document_profile,
                    document_profile_source=document_profile_source,
                    chunk_policy_id=str(load_res.chunks[0].metadata.get("chunk_policy_id") or ""),
                    chunk_ids=chunk_ids,
                )
                self._index.setdefault("files", {})[fhash] = {
                    "file_path": rec.file_path, "file_name": rec.file_name,
                    "file_size": rec.file_size, "category": rec.category,
                    "kb_name": kb_name, "doc_category": doc_category,
                    "document_entity": document_entity,
                    "document_profile": rec.document_profile,
                    "document_profile_source": rec.document_profile_source,
                    "chunk_policy_id": rec.chunk_policy_id,
                    "last_modified": rec.last_modified, "added_at": rec.added_at,
                    "chunk_ids": rec.chunk_ids,
                }
                self._consume_document_profile_selection(rel)
                new += 1
                logger.info(
                    "新文件: %s (%s) | %d 块 | %.2fs",
                    fp.name, load_res.category, len(load_res.chunks), elapsed_store,
                )

            except Exception as e:
                err += 1
                logger.error("处理异常 %s: %s", fp, e)
                details.append({
                    "status": "error",
                    "path": str(fp),
                    "reason_code": "UNEXPECTED_ERROR",
                    "locator": None,
                    "message": str(e),
                })

        cleaned = self._clean_removed(watch_dir)
        self._save_index()

        if self._refresh_retrieval and (
            new or any(item.should_rebuild_bm25 for item in cleaned)
        ):
            BM25Store().rebuild()
            from rag_knowledge.services.query_cache import clear_query_cache
            clear_query_cache()

        elapsed = time.time() - t0
        parts = [f"新增 {new}"]
        if skip: parts.append(f"跳过 {skip}")
        if queued_cnt: parts.append(f"排队 {queued_cnt}")
        if excluded_cnt: parts.append(f"排除 {excluded_cnt}")
        if err: parts.append(f"失败 {err}")
        if cleaned: parts.append(f"清理 {len(cleaned)}")
        logger.info("扫描完成 | %s | %.2fs", " / ".join(parts), elapsed)

        return {
            "new_files": new,
            "skipped_files": skip,
            "queued_files": queued_cnt,
            "excluded_files": excluded_cnt,
            "errors": err,
            "details": details,
        }

    def _clean_removed(self, base: Path) -> list:
        """清理已被删除文件的索引记录，同时删除对应向量和决策。"""
        files = self._index.get("files", {})
        removed_hashes = [h for h, r in files.items() if not (base / r["file_path"]).exists()]
        cleaned = []
        for file_hash in removed_hashes:
            r = files[file_hash]
            if getattr(self, "_decision_store", None) is not None:
                self._decision_store.replace_for_file(file_path=r["file_path"], file_hash=file_hash, decisions=[])
            cleaned.append(
                cleanup_indexed_file(
                    file_hash,
                    data_dir=self._cfg.data_dir,
                    index_data=self._index,
                    persist=False,
                )
            )
        if getattr(self, "_decision_store", None) is not None:
            self._decision_store.prune_missing(base)
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
                index = json.loads(self._index_path.read_text(encoding="utf-8"))
                changed = index.get("version") != _INDEX_VERSION
                index["version"] = _INDEX_VERSION
                files = index.setdefault("files", {})
                for entry in files.values():
                    if "document_profile_source" not in entry:
                        entry["document_profile_source"] = "legacy"
                        changed = True
                    if "chunk_policy_id" not in entry:
                        entry["chunk_policy_id"] = ""
                        changed = True
                if changed:
                    self._atomic_write_json(self._index_path, index)
                return index
            except Exception as e:
                logger.warning("索引文件损坏，将重建: %s", e)
        return {"version": _INDEX_VERSION, "files": {}}

    def _save_index(self):
        try:
            self._atomic_write_json(self._index_path, self._index)
        except Exception as e:
            logger.error("保存索引失败: %s", e)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

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

    @staticmethod
    def _load_json_map(path: Path) -> dict:
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}

    def _save_profile_selection_map(self) -> None:
        self._profile_selection_map_path.write_text(
            json.dumps(self._profile_selection_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_document_profile(self, relative_path: str, document_profile: str) -> None:
        """Persist an uploader's explicit selection until the scanner consumes it."""
        profile = normalize_document_profile(document_profile).value
        self._profile_selection_map[_normalize_profile_path(relative_path)] = profile
        self._save_profile_selection_map()

    def _resolve_document_profile(self, rel: str, file_hash: str) -> str:
        return self._resolve_document_profile_with_source(rel, file_hash)[0]

    def _resolve_document_profile_with_source(self, rel: str, file_hash: str) -> tuple[str, str]:
        rel = _normalize_profile_path(rel)
        explicit = self._profile_selection_map.get(rel, "")
        if explicit:
            return normalize_document_profile(explicit).value, "explicit"

        inherited = self._rebuild_profile_map.get(rel) or self._rebuild_hash_profile_map.get(file_hash, "")
        if inherited:
            return normalize_document_profile(inherited).value, "inherited"

        mapped = self._profile_map.get(rel, "")
        if isinstance(mapped, dict):
            mapped = mapped.get("document_profile", "")
        if mapped:
            return normalize_document_profile(mapped).value, "profile_map"
        return normalize_document_profile(None).value, "default"

    def _consume_document_profile_selection(self, rel: str) -> None:
        if self._profile_selection_map.pop(_normalize_profile_path(rel), None) is not None:
            self._save_profile_selection_map()

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
