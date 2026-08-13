#!/usr/bin/env python3
"""Import portable records.jsonl into empty Chroma on Linux (re-embed via VectorStore)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from langchain_core.documents import Document

BATCH = 32
RECORDS = Path("/tmp/chroma_export/records.jsonl")


def _clean_meta(meta: dict, doc_id: str) -> dict:
    out = {}
    for k, v in (meta or {}).items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    out.setdefault("review_status", "approved")
    out.setdefault("kb_name", "文章附件")
    out["chunk_uid"] = doc_id
    out["chunk_id"] = doc_id
    return out


def main() -> int:
    from rag_knowledge.repository.vector_store import VectorStore
    from rag_knowledge.services.bm25_store import BM25Store

    VectorStore._instance = None
    BM25Store._instance = None

    if not RECORDS.is_file():
        raise SystemExit(f"missing {RECORDS}")

    rows = []
    with RECORDS.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"loaded records={len(rows)}", flush=True)

    vs = VectorStore()
    before = vs.count()
    print(f"collection_before={before}", flush=True)
    if before > 0:
        raise SystemExit(f"refusing import into non-empty collection count={before}")

    t0 = time.time()
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        docs = []
        for r in chunk:
            doc_id = str(r["id"])
            docs.append(
                Document(
                    page_content=r.get("document") or "",
                    metadata=_clean_meta(r.get("metadata") or {}, doc_id),
                )
            )
        vs.add_chunks(docs)
        done += len(chunk)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        print(f"imported {done}/{len(rows)} rate={rate:.1f}/s", flush=True)

    after = vs.count()
    print(f"collection_after={after}", flush=True)
    bm = BM25Store()
    bm.rebuild()
    print(f"bm25_docs={len(getattr(bm, '_docs', []) or [])}", flush=True)
    print("DONE", flush=True)
    return 0 if after == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
