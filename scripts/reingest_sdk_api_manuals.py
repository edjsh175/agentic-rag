"""Re-ingest StampWebRTC/WebGL interface manuals with api_doc profile.

Stop the backend first (avoid concurrent chroma writes). Usage:

  $env:RAG_CONFIG=\"config-local.ini\"
  $env:PYTHONPATH=(Get-Location).Path
  .\\venv\\Scripts\\python.exe scripts/reingest_sdk_api_manuals.py
  .\\venv\\Scripts\\python.exe scripts/reingest_sdk_api_manuals.py --approve
"""

from __future__ import annotations

import argparse
import json
import sys
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


def _match(entry: dict) -> bool:
    name = str(entry.get("file_name") or "")
    path = str(entry.get("file_path") or "")
    return any(name.endswith(suffix) or path.endswith(suffix) for suffix in TARGET_SUFFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve", action="store_true", help="Mark re-ingested chunks approved")
    parser.add_argument("--dry-run", action="store_true", help="Only list matching file_index entries")
    args = parser.parse_args()

    from rag_knowledge.config import Config
    from rag_knowledge.services.index_cleanup import cleanup_indexed_file
    from rag_knowledge.services.scanner import DirectoryScanner

    cfg = Config()
    index_path = cfg.data_dir / "file_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    files = index.get("files") or {}
    targets = [(fhash, entry) for fhash, entry in files.items() if isinstance(entry, dict) and _match(entry)]
    print(f"matched {len(targets)} indexed manuals")
    for fhash, entry in targets:
        print(f"  - {entry.get('file_path')} chunks={len(entry.get('chunk_ids') or [])} profile={entry.get('document_profile')}")
    if args.dry_run:
        return 0

    rebuild_bm25 = False
    for fhash, _entry in targets:
        result = cleanup_indexed_file(fhash, data_dir=cfg.data_dir)
        print(f"cleaned {result.file_name}: deleted_chunks={result.deleted_chunks}")
        rebuild_bm25 = rebuild_bm25 or result.should_rebuild_bm25

    scanner = DirectoryScanner(refresh_retrieval=True)
    scan_result = scanner.scan()
    print("scan", scan_result)

    if args.approve:
        from rag_knowledge.repository.vector_store import VectorStore

        store = VectorStore()
        # Re-load index after scan
        index = json.loads(index_path.read_text(encoding="utf-8"))
        files = index.get("files") or {}
        chunk_ids: list[str] = []
        for entry in files.values():
            if isinstance(entry, dict) and _match(entry):
                chunk_ids.extend(entry.get("chunk_ids") or [])
        if chunk_ids:
            store.update_metadata(chunk_ids, {"review_status": "approved"})
            print(f"approved {len(chunk_ids)} chunks")
            from rag_knowledge.services.bm25_store import BM25Store
            from rag_knowledge.services.query_cache import clear_query_cache

            BM25Store().rebuild()
            clear_query_cache()
        else:
            print("no chunks to approve")
    elif rebuild_bm25:
        from rag_knowledge.services.bm25_store import BM25Store

        BM25Store().rebuild()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
