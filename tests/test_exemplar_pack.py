# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.services.graph_extraction.exemplar_pack import (
    DEFAULT_MAX_CHARS,
    clear_exemplar_cache,
    format_exemplars_for_prompt,
    load_pack,
    load_universal_pack,
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


def test_universal_pack_always_present_for_server():
    clear_exemplar_cache()
    assert load_universal_pack().get("pack_id") == "pattern_universal_v1"
    assert pack_path_for_category("StampServer") is None
    text = format_exemplars_for_prompt("StampServer")
    assert text != "(none)"
    assert "uni-deploy-proc-command" in text
    assert "st-proc-new-project" not in text


def test_format_exemplars_truncates_to_budget():
    clear_exemplar_cache()
    text = format_exemplars_for_prompt("StampTools", max_chars=400)
    assert len(text) <= 400
    full = format_exemplars_for_prompt("StampTools", max_chars=DEFAULT_MAX_CHARS)
    assert "uni-proc-under-tool" in full
    assert "GOOD extraction JSON" in full
