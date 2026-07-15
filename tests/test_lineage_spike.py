"""Lineage spike smoke test on generated fixture DOCX."""
from __future__ import annotations

from pathlib import Path

from scripts.spike_parse_lineage import _ensure_fixture, emit_lineage


def test_lineage_spike_bidirectional_and_no_silent_drop(tmp_path: Path):
    fixture = _ensure_fixture(tmp_path / "lineage_spike.docx")
    result = emit_lineage(fixture, tmp_path / "chunk_audit")
    assert result["summary"]["双向追溯_ok"] is True
    assert result["summary"]["无静默删除_ok"] is True
    run_dir = Path(result["run_dir"])
    for name in (
        "manifest.json",
        "raw_blocks.jsonl",
        "canonical_elements.jsonl",
        "structure_decisions.jsonl",
        "transformations.jsonl",
        "content_decisions.jsonl",
        "final_chunk_lineage.jsonl",
        "quarantine.jsonl",
        "summary.json",
    ):
        assert (run_dir / name).exists()
