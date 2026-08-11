# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.services.graph_extraction.exemplar_pack import (
    DEFAULT_MAX_CHARS,
    clear_exemplar_cache,
    format_exemplars_for_prompt,
    load_pack,
    pack_path_for_category,
)


def test_stamptools_pack_loads_and_has_core_scenarios():
    clear_exemplar_cache()
    pack = load_pack("StampTools")
    assert pack.get("pack_id") == "stamptools_v1"
    ids = {item["id"] for item in pack["exemplars"]}
    assert "st-proc-new-project" in ids
    assert "st-format-data-spec" in ids
    assert "st-no-gui-configitem" in ids
    assert "st-no-reparent-backbone" in ids


def test_other_category_has_no_stamptools_pack():
    clear_exemplar_cache()
    assert pack_path_for_category("StampServer") is None
    assert load_pack("StampServer") == {}
    assert format_exemplars_for_prompt("StampServer") == "(none)"
    assert format_exemplars_for_prompt("博客") == "(none)"


def test_format_exemplars_truncates_to_budget():
    clear_exemplar_cache()
    text = format_exemplars_for_prompt("StampTools", max_chars=400)
    assert len(text) <= 400
    assert "st-proc-new-project" in text or text.endswith("... (truncated)")
    full = format_exemplars_for_prompt("StampTools", max_chars=DEFAULT_MAX_CHARS)
    assert "st-proc-new-project" in full
    assert "GOOD extraction JSON" in full
    assert "不要把「纹理格式」" in full or "ConfigItem" in full
