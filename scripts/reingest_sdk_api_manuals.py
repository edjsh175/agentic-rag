"""Re-ingest StampWebRTC/WebGL interface manuals with api_doc profile.

Stop the backend first (avoid concurrent chroma writes). Usage:

  Remove-Item Env:RAG_CONFIG -ErrorAction SilentlyContinue   # use config.ini remote embed
  $env:PYTHONPATH=(Get-Location).Path
  .\\venv\\Scripts\\python.exe scripts/reingest_sdk_api_manuals.py --dry-run
  .\\venv\\Scripts\\python.exe scripts/reingest_sdk_api_manuals.py --approve
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_SUFFIXES = (
    "StampGIS平台WebRTC接口说明书_2026_05_26_15_45_50.docx",
    "StampGIS平台WebGL接口说明书.docx",
    "StampGIS平台WebGL接口说明书_2026_05_26_15_45_46.docx",
    "StampGIS平台WebRTC接口说明书.pdf",
    "GIS平台WebRTC接口说明书.pdf",
)

DOC_CATEGORY_BY_SUFFIX = {
    "StampGIS平台WebRTC接口说明书_2026_05_26_15_45_50.docx": "StampWebRTC",
    "StampGIS平台WebRTC接口说明书.pdf": "StampWebRTC",
    "GIS平台WebRTC接口说明书.pdf": "StampWebRTC",
    "StampGIS平台WebGL接口说明书.docx": "StampWebGL",
    "StampGIS平台WebGL接口说明书_2026_05_26_15_45_46.docx": "StampWebGL",
}

EMBED_BATCH = 24


def _match(entry: dict) -> bool:
    name = str(entry.get("file_name") or "")
    path = str(entry.get("file_path") or "")
    return any(name.endswith(suffix) or path.endswith(suffix) for suffix in TARGET_SUFFIXES)


def _category_for(path: Path) -> str:
    for suffix, cat in DOC_CATEGORY_BY_SUFFIX.items():
        if path.name.endswith(suffix):
            return cat
    return "StampWebRTC" if "WebRTC" in path.name else "StampWebGL"


def _ingest_file(path: Path, *, approve: bool) -> int:
    from rag_knowledge.services.loader import FileLoader
    from rag_knowledge.repository.vector_store import VectorStore

    loader = FileLoader()
    store = VectorStore()
    chunks, _category = loader.load(str(path), document_profile="api_doc")
    doc_cat = _category_for(path)
    parent = path.parent.name
    for doc in chunks:
        meta = dict(doc.metadata or {})
        meta["kb_name"] = "文章附件"
        meta["kb_path"] = parent
        meta["doc_category"] = doc_cat
        meta["document_profile"] = "api_doc"
        meta["document_profile_source"] = "profile_map"
        if approve:
            meta["review_status"] = "approved"
        else:
            meta.setdefault("review_status", "pending")
        doc.metadata = meta

    ids: list[str] = []
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                ids.extend(store.add_chunks(batch))
                print(f"  batch {start}-{start + len(batch)} ok")
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 — retry remote embed flakes
                last_error = exc
                print(f"  batch {start} attempt {attempt + 1} FAIL: {exc}")
                time.sleep(2 + attempt * 2)
        if last_error is not None:
            raise RuntimeError(f"failed batch at {start} for {path.name}") from last_error

    from rag_knowledge.config import Config

    cfg = Config()
    index_path = cfg.data_dir / "file_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    files = index.setdefault("files", {})
    fhash = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = str(path.relative_to(cfg.watch_dir))
    files[fhash] = {
        "file_path": rel,
        "file_name": path.name,
        "chunk_ids": ids,
        "document_profile": "api_doc",
        "document_profile_source": "profile_map",
        "doc_category": doc_cat,
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"indexed {path.name}: chunks={len(ids)} cat={doc_cat}")
    return len(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve", action="store_true", help="Write chunks as approved")
    parser.add_argument("--dry-run", action="store_true", help="Only list target files")
    args = parser.parse_args()

    from rag_knowledge.config import Config
    from rag_knowledge.services.index_cleanup import cleanup_indexed_file

    cfg = Config()
    watch = Path(cfg.watch_dir)
    targets_on_disk = []
    for suffix in TARGET_SUFFIXES:
        hits = list(watch.rglob(f"*{suffix}"))
        targets_on_disk.extend(hits)
    # de-dupe
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for path in targets_on_disk:
        key = str(path.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)

    print(f"disk targets {len(unique_paths)}")
    for path in unique_paths:
        print(f"  - {path.relative_to(watch)}")
    if args.dry_run:
        return 0

    index_path = cfg.data_dir / "file_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    files = index.get("files") or {}
    for fhash, entry in list(files.items()):
        if isinstance(entry, dict) and _match(entry):
            result = cleanup_indexed_file(fhash, data_dir=cfg.data_dir)
            print(f"cleaned {result.file_name}: deleted_chunks={result.deleted_chunks}")

    total = 0
    for path in unique_paths:
        print(f"=== ingest {path.name}")
        total += _ingest_file(path, approve=args.approve)

    from rag_knowledge.services.bm25_store import BM25Store
    from rag_knowledge.services.query_cache import clear_query_cache

    BM25Store().rebuild()
    clear_query_cache()
    print(f"DONE total_chunks={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
