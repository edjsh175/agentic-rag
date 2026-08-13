#!/usr/bin/env python3
"""Export Chroma collection to portable numpy/jsonl for cross-OS restore."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from rag_knowledge.repository.vector_store import VectorStore


def main() -> int:
    out = Path(os.environ.get("CHROMA_EXPORT_DIR", r"C:\Temp\chroma_export_rag_knowledge"))
    out.mkdir(parents=True, exist_ok=True)
    VectorStore._instance = None
    vs = VectorStore()
    col = vs.get_chroma()._collection
    total = col.count()
    print(f"exporting count={total} -> {out}")

    # page through get
    page = 500
    offset = 0
    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []
    all_embs: list[list[float]] = []
    while offset < total:
        # chroma get supports limit/offset in some versions; fallback to full get once
        break
    got = col.get(include=["documents", "metadatas", "embeddings"])
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    embs = got.get("embeddings")
    if embs is None:
        raise SystemExit("embeddings missing from collection.get")
    print(f"got ids={len(ids)} docs={len(docs)} metas={len(metas)} embs={len(embs)}")
    if len(ids) != total:
        print(f"WARN count mismatch total={total} got={len(ids)}")

    np.save(out / "embeddings.npy", np.asarray(embs, dtype=np.float32))
    with (out / "records.jsonl").open("w", encoding="utf-8") as f:
        for i, _id in enumerate(ids):
            f.write(
                json.dumps(
                    {
                        "id": _id,
                        "document": docs[i],
                        "metadata": metas[i] or {},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    meta = {
        "collection": col.name,
        "count": len(ids),
        "embedding_dim": int(np.asarray(embs).shape[1]) if len(embs) else 0,
    }
    (out / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("manifest", meta)
    print("files", sorted(p.name for p in out.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
