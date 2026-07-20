"""Recalibrate evaluation dataset chunk IDs after a knowledge base rebuild."""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.evaluation.dataset_health import _target_matches, _source_matches


def _freeze_ab_results() -> None:
    """Freeze current A/B test results by archiving them with a timestamp."""
    src = Path("data/retrieval_ab_results.json")
    if not src.exists():
        print("No A/B results found at data/retrieval_ab_results.json, skipping archiving.")
        return

    archive_dir = Path("data/archive/retrieval_ab_results")
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = archive_dir / f"retrieval_ab_results_{ts}.json"
    shutil.copy2(src, dst)
    print(f"Successfully archived A/B results to {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recalibrate dataset chunk IDs")
    parser.add_argument("dataset", help="Path to the evaluation dataset JSON file")
    parser.add_argument(
        "--output",
        help="Path to output the recalibrated dataset",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input dataset file directly",
    )
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="Allow writing output even if there are unresolved targets",
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

    # Validate that at least one entry has expected_targets
    has_targets = any("expected_targets" in entry for entry in data)
    if not has_targets:
        print(
            "Error: dataset has no expected_targets field. Run migrate_eval_dataset.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 1: Freeze old results
    print("Step 1: Freezing current A/B results...")
    _freeze_ab_results()

    # Initialize config and vector store
    print("Initializing VectorStore and loading chunks from database...")
    cfg = Config()
    store = VectorStore()
    chroma = store.get_chroma()
    collection = chroma._collection

    res = collection.get(include=["documents", "metadatas"])
    ids = res.get("ids") or []
    documents = res.get("documents") or []
    metadatas = res.get("metadatas") or []
    
    all_chunks = []
    fingerprint_map = {}  # content_fingerprint -> chunk_id
    
    for idx, cid in enumerate(ids):
        doc_text = documents[idx]
        metadata = metadatas[idx] or {}
        chunk = {
            "id": cid,
            "document": doc_text,
            "metadata": metadata
        }
        all_chunks.append(chunk)
        
        # Calculate fingerprint for current database chunks
        content_fp = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()[:16]
        if content_fp not in fingerprint_map:
            fingerprint_map[content_fp] = cid

    print(f"Loaded {len(all_chunks)} chunks from current database.")

    # Step 2 & 3: Recalibrate
    print("Step 2 & 3: Recalibrating chunk IDs...")
    recalibrated_count = 0
    resolved_by_fp = 0
    resolved_by_loose = 0
    unresolved_count = 0

    for idx, entry in enumerate(data):
        expected_targets = entry.get("expected_targets") or []
        new_chunk_ids = []

        for target in expected_targets:
            # 1. Exact match via content_fingerprint
            fp = target.get("content_fingerprint")
            if fp and fp in fingerprint_map:
                new_chunk_ids.append(fingerprint_map[fp])
                resolved_by_fp += 1
                continue

            # 2. Loose fallback match using metadata and keywords
            matched_chunk = None
            for chunk in all_chunks:
                if _target_matches(chunk, target):
                    matched_chunk = chunk
                    break
            
            if matched_chunk:
                new_chunk_ids.append(matched_chunk["id"])
                resolved_by_loose += 1
            else:
                unresolved_count += 1
                print(
                    f"Warning: unable to resolve target (source: {target.get('source')}, "
                    f"section_path: {target.get('section_path')}) for question: {entry.get('question', '')[:40]}"
                )

        # Update IDs
        entry["chunk_ids"] = new_chunk_ids
        entry["relevant_chunk_ids"] = new_chunk_ids
        recalibrated_count += 1

    # Save
    output_path = Path(args.output) if args.output else dataset_path

    # Write back if resolved or allowed
    if unresolved_count > 0 and not args.allow_unresolved:
        print(f"\nError: Recalibration has {unresolved_count} unresolved targets. Writing aborted.", file=sys.stderr)
        print("Use --allow-unresolved to force writing the output.", file=sys.stderr)
        sys.exit(1)

    if args.in_place and output_path == dataset_path:
        # Backup input file
        bak_file = dataset_path.with_suffix(".json.bak")
        if not bak_file.exists():
            shutil.copy2(dataset_path, bak_file)
            print(f"Saved backup of input dataset to {bak_file}")

    print(f"Writing recalibrated dataset to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\nRecalibration Completed:")
    print(f"  Total questions processed: {len(data)}")
    print(f"  Resolved by fingerprint:   {resolved_by_fp}")
    print(f"  Resolved by loose match:   {resolved_by_loose}")
    print(f"  Unresolved targets:        {unresolved_count}")


if __name__ == "__main__":
    main()
