"""Readonly Chunk health audit CLI for Round 0A.

Usage:
  .\\venv\\Scripts\\python.exe scripts/audit_chunk_health.py
  .\\venv\\Scripts\\python.exe scripts/audit_chunk_health.py --no-reparse
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_knowledge.services.chunk_health_audit import ChunkHealthAuditor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/3_待办清单/chunk-foundation-round0a"),
        help="Directory for JSON/Markdown audit outputs",
    )
    parser.add_argument(
        "--no-reparse",
        action="store_true",
        help="Skip source-document reparse/filter comparison",
    )
    parser.add_argument(
        "--max-filter-samples",
        type=int,
        default=80,
        help="Max filtered samples retained per document",
    )
    parser.add_argument(
        "--annotation-sample-size",
        type=int,
        default=160,
        help="Candidate count for heading/body/garbage annotation set",
    )
    parser.add_argument(
        "--report-label",
        default="Round 0A",
        help="Label rendered in the Markdown report title",
    )
    args = parser.parse_args()

    auditor = ChunkHealthAuditor()
    report = auditor.run(
        reparse_sources=not args.no_reparse,
        max_filter_samples=args.max_filter_samples,
        annotation_sample_size=args.annotation_sample_size,
    )
    report["report_label"] = args.report_label
    paths = auditor.write_reports(report, args.output_dir)
    overview = report.get("overview") or {}
    reparse = report.get("reparse") or {}
    print(f"total_chunks={overview.get('total_chunks')}")
    print(f"length_p50={overview.get('length_p50')}")
    print(f"pct_lt_100={overview.get('pct_lt_100')}")
    print(f"empty_section_path_pct={overview.get('empty_section_path_pct')}")
    if reparse.get("enabled"):
        print(
            f"reparse_filter_rate_pct={reparse.get('filter_rate_pct')} "
            f"pre={reparse.get('pre_filter_chunks')} post={reparse.get('post_filter_chunks')}"
        )
    print(f"corpus_snapshot_hash={report.get('corpus_snapshot_hash')}")
    for key, path in paths.items():
        print(f"{key}={path}")


if __name__ == "__main__":
    main()
