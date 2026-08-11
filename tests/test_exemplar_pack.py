# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.services.graph_extraction.exemplar_pack import (
    DEFAULT_MAX_CHARS,
    clear_exemplar_cache,
    exemplar_root,
    format_exemplars_for_prompt,
    load_universal_pack,
)


def test_universal_pack_is_sole_source():
    clear_exemplar_cache()
    pack = load_universal_pack()
    assert pack.get("pack_id") == "pattern_universal_v1"
    assert pack.get("version") == "1.1"
    ids = {item["id"] for item in pack["exemplars"]}
    assert "uni-proc-under-tool" in ids
    assert "uni-proc-resume" in ids
    assert "uni-proc-no-fa" in ids
    assert "uni-deploy-proc-command" in ids
    assert "uni-format-table" in ids
    assert not (exemplar_root() / "stamptools_v1.json").exists()


def test_universal_pack_only_for_all_categories():
    clear_exemplar_cache()
    for category in ("StampTools", "StampServer", "StampWebRTC", ""):
        text = format_exemplars_for_prompt(category)
        assert text != "(none)"
        assert "uni-deploy-proc-command" in text
        assert "uni-proc-resume" in text
        assert "st-proc-new-project" not in text
        assert "Category-specific exemplars" not in text


def test_format_exemplars_identical_across_categories():
    clear_exemplar_cache()
    a = format_exemplars_for_prompt("StampTools")
    b = format_exemplars_for_prompt("StampServer")
    assert a == b


def test_format_exemplars_truncates_to_budget():
    clear_exemplar_cache()
    text = format_exemplars_for_prompt("StampTools", max_chars=400)
    assert len(text) <= 400
    full = format_exemplars_for_prompt("StampTools", max_chars=DEFAULT_MAX_CHARS)
    assert "uni-proc-under-tool" in full
    assert "GOOD extraction JSON" in full
