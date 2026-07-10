#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CLI for Task 8.1 graph fact gate validation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.task81_graph_gate import Task81GraphGateValidator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Task 8.1 graph fact gate")
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-global-quality", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = RelationalDB()
    if args.db_path:
        from rag_knowledge.config import Config

        config = Config()
        config._config.set("relational_db", "db_path", str(args.db_path))
    report = Task81GraphGateValidator(db).validate(include_global_quality=not args.skip_global_quality)
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
