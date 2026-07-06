"""Preview or apply product-domain categories to existing knowledge chunks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_knowledge.services.chunk_admin import migrate_doc_categories


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write categories to Chroma and data/file_index.json. Without this flag, only preview.",
    )
    args = parser.parse_args()
    print(json.dumps(migrate_doc_categories(apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
