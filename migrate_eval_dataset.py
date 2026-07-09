"""Migrate old format evaluation datasets to include expected_targets with automatic loose fallback calibration."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
import jieba

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore


def _extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """用 jieba 提取高频关键词。"""
    words = [w for w in jieba.cut(text) if len(w) >= 2]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return sorted(freq, key=freq.get, reverse=True)[:top_n]


def _source_matches(metadata: dict, source: str) -> bool:
    expected = str(source or "").strip().replace("\\", "/")
    if not expected:
        return False
    candidates = [
        str(metadata.get("source") or ""),
        str(metadata.get("file_path") or ""),
        str(metadata.get("file_name") or ""),
    ]
    normalized = [candidate.strip().replace("\\", "/") for candidate in candidates]
    return any(candidate == expected or candidate.endswith(f"/{expected}") or expected.endswith(f"/{candidate}") for candidate in normalized)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate old eval dataset to new format with loose fallback calibration")
    parser.add_argument("dataset", help="Path to the evaluation dataset JSON file")
    parser.add_argument(
        "--output",
        help="Path to output the migrated dataset (must specify this or --in-place)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input dataset file directly",
    )
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="Allow writing output even if there are unresolved entries",
    )
    args = parser.parse_args()

    if not args.output and not args.in_place:
        print("Error: Please specify either --output <path> or --in-place to proceed.", file=sys.stderr)
        sys.exit(1)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Error: dataset file {dataset_path} does not exist", file=sys.stderr)
        sys.exit(1)

    print(f"Loading dataset from {dataset_path}...")
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: dataset must be a list of objects", file=sys.stderr)
        sys.exit(1)

    print("Initializing VectorStore and Chroma client...")
    cfg = Config()
    store = VectorStore()
    chroma = store.get_chroma()
    collection = chroma._collection

    # Create backup if not already backed up
    backup_path = dataset_path.with_suffix(".json.bak")
    if not backup_path.exists():
        print(f"Creating backup at {backup_path}...")
        shutil.copy2(dataset_path, backup_path)
    else:
        print(f"Backup already exists at {backup_path}, reading from current file.")

    # Load all chunks for loose matching fallback
    print("Loading all chunks from Chroma for fallback mapping...")
    res = collection.get(include=["documents", "metadatas"])
    ids = res.get("ids") or []
    documents = res.get("documents") or []
    metadatas = res.get("metadatas") or []
    all_chunks = []
    for idx, cid in enumerate(ids):
        all_chunks.append({
            "id": cid,
            "document": documents[idx],
            "metadata": metadatas[idx] or {}
        })
    print(f"Loaded {len(all_chunks)} chunks from database.")

    updated_count = 0
    by_id_count = 0
    by_loose_count = 0
    unresolved_count = 0

    print("Migrating entries...")
    for idx, entry in enumerate(data):
        relevant_ids = entry.get("relevant_chunk_ids") or []
        if not relevant_ids and "chunk_ids" in entry:
            relevant_ids = entry["chunk_ids"]

        expected_targets = []
        new_chunk_ids = []

        for cid in relevant_ids:
            # 1. Try exact ID lookup
            exact_res = collection.get(ids=[cid], include=["documents", "metadatas"])
            exact_ids = exact_res.get("ids") or []
            if exact_ids:
                doc_text = exact_res["documents"][0] if exact_res.get("documents") else ""
                metadata = exact_res["metadatas"][0] if exact_res.get("metadatas") else {}
                
                content_fp = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()[:16]
                section_path = metadata.get("section_path", "") or metadata.get("section_title", "")
                source_val = metadata.get("source", "")
                chunk_keywords = _extract_keywords(doc_text, top_n=5)

                expected_targets.append({
                    "source": source_val,
                    "section_path": section_path,
                    "keywords": chunk_keywords,
                    "content_fingerprint": content_fp,
                })
                new_chunk_ids.append(cid)
                by_id_count += 1
                continue

            # 2. Try loose fallback mapping if exact ID lookup fails
            source_val = entry.get("source", "")
            q = entry.get("question", "")
            
            candidates = [c for c in all_chunks if _source_matches(c["metadata"], source_val)]
            if not candidates and source_val:
                src_clean = Path(source_val).stem.replace("用户手册", "").replace("用户指南", "").strip()
                if len(src_clean) > 2:
                    candidates = [c for c in all_chunks if src_clean in str(c["metadata"].get("source", ""))]

            if candidates:
                q_words = [w for w in jieba.cut(q) if len(w) >= 2]
                best_chunk = None
                max_overlap = -1
                
                for c in candidates:
                    doc_text = c["document"]
                    overlap = sum(1 for w in q_words if w in doc_text)
                    if overlap > max_overlap:
                        max_overlap = overlap
                        best_chunk = c
                
                if best_chunk and max_overlap > 0:
                    doc_text = best_chunk["document"]
                    metadata = best_chunk["metadata"]
                    content_fp = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()[:16]
                    section_path = metadata.get("section_path", "") or metadata.get("section_title", "")
                    actual_source = metadata.get("source", "") or source_val
                    chunk_keywords = _extract_keywords(doc_text, top_n=5)

                    expected_targets.append({
                        "source": actual_source,
                        "section_path": section_path,
                        "keywords": chunk_keywords,
                        "content_fingerprint": content_fp,
                    })
                    new_chunk_ids.append(best_chunk["id"])
                    by_loose_count += 1
                    continue

            # 3. Unresolved
            unresolved_count += 1
            print(f"Warning: could not resolve chunk ID {cid} (source: {source_val}) for question: {q[:40]}")

        entry["expected_targets"] = expected_targets
        entry["chunk_ids"] = new_chunk_ids
        entry["relevant_chunk_ids"] = new_chunk_ids  # Update relevant_chunk_ids to match new_chunk_ids
        updated_count += 1

        if (idx + 1) % 50 == 0 or (idx + 1) == len(data):
            print(f"Processed {idx + 1}/{len(data)} entries...")

    output_path = Path(args.output) if args.output else dataset_path

    # Write back if resolved or allowed
    if unresolved_count > 0 and not args.allow_unresolved:
        print(f"\nError: Migration has {unresolved_count} unresolved entries. Writing aborted.", file=sys.stderr)
        print("Use --allow-unresolved to force writing the output.", file=sys.stderr)
        sys.exit(1)

    # Backup if overwriting in place
    if args.in_place and output_path == dataset_path:
        backup_path = dataset_path.with_suffix(".json.bak")
        if not backup_path.exists():
            print(f"Creating backup at {backup_path}...")
            shutil.copy2(dataset_path, backup_path)

    print(f"Writing migrated dataset to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\nMigration Completed:")
    print(f"  Total entries:           {len(data)}")
    print(f"  Updated entries:         {updated_count}")
    print(f"  Resolved by exact ID:    {by_id_count}")
    print(f"  Resolved by loose match: {by_loose_count}")
    print(f"  Unresolved entries:      {unresolved_count}")


if __name__ == "__main__":
    main()
