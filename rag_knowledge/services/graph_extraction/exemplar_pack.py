"""Load curated extraction exemplar packs for LLM few-shot injection."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULT_MAX_CHARS = 2500
_NONE = "(none)"

# doc_category -> pack filename under data/extraction_exemplars/
_CATEGORY_PACKS: dict[str, str] = {
    "StampTools": "stamptools_v1.json",
}


def exemplar_root() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "extraction_exemplars"


@lru_cache(maxsize=8)
def _load_pack_file(path_str: str) -> dict:
    path = Path(path_str)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"exemplar pack root must be object: {path}")
    exemplars = data.get("exemplars")
    if exemplars is not None and not isinstance(exemplars, list):
        raise ValueError(f"exemplar pack exemplars must be a list: {path}")
    return data


def pack_path_for_category(doc_category: str) -> Path | None:
    name = _CATEGORY_PACKS.get(str(doc_category or "").strip())
    if not name:
        return None
    path = exemplar_root() / name
    return path if path.is_file() else None


def load_pack(doc_category: str) -> dict:
    path = pack_path_for_category(doc_category)
    if path is None:
        return {}
    return _load_pack_file(str(path.resolve()))


def format_exemplars_for_prompt(
    doc_category: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Return prompt section text for {extraction_exemplars}; (none) when no pack."""
    pack = load_pack(doc_category)
    exemplars = pack.get("exemplars") or []
    if not exemplars:
        return _NONE

    lines: list[str] = [
        "Curated golden exemplars for this doc_category (imitate patterns; do not copy StampTools entity names onto other products):",
    ]
    for item in exemplars:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("id") or "").strip() or "unnamed"
        scenario = str(item.get("scenario") or "").strip()
        section = str(item.get("section_path") or "").strip()
        excerpt = str(item.get("content_excerpt") or "").strip()
        good = item.get("good") if isinstance(item.get("good"), dict) else {}
        bad = item.get("bad") if isinstance(item.get("bad"), list) else []
        lines.append(f"## Exemplar {eid}" + (f" ({scenario})" if scenario else ""))
        if section:
            lines.append(f"section_path: {section}")
        if excerpt:
            lines.append(f"content_excerpt:\n{excerpt}")
        lines.append("GOOD extraction JSON:")
        lines.append(json.dumps(good, ensure_ascii=False, indent=2))
        if bad:
            lines.append("BAD (do NOT do):")
            for tip in bad:
                tip_s = str(tip or "").strip()
                if tip_s:
                    lines.append(f"- {tip_s}")
        lines.append("")

    text = "\n".join(lines).rstrip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... (truncated)"


def clear_exemplar_cache() -> None:
    _load_pack_file.cache_clear()
