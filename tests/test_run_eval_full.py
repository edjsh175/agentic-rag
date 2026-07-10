import pytest

import run_eval_full


def test_existing_dataset_requires_overwrite(tmp_path):
    output = tmp_path / "fixed.json"
    output.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="already exists"):
        run_eval_full.main(["--output", str(output)])


def test_default_output_is_timestamped(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval_full, "DEFAULT_OUTPUT_DIR", tmp_path)
    path = run_eval_full.resolve_output_path(None, now="20260710-120000")
    assert path == tmp_path / "eval_dataset_20260710-120000.json"
