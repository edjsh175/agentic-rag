"""Entity-type peer eligibility: binaries are Utility, not peer Tools under a Product."""
from __future__ import annotations

import re

_UTILITY_SUFFIX_RE = re.compile(
    r"(?i)\.(exe|dll|so|bat|cmd|ps1|sh)$"
)


def looks_like_utility_name(name: str) -> bool:
    """Heuristic: installable/runtime binaries are utilities, not product Tools."""
    text = str(name or "").strip()
    if not text:
        return False
    return bool(_UTILITY_SUFFIX_RE.search(text))


def coerce_entity_type(name: str, entity_type: str) -> str:
    """Downgrade misclassified Tool binaries to Utility."""
    etype = str(entity_type or "").strip()
    if etype == "Tool" and looks_like_utility_name(name):
        return "Utility"
    return etype


def utility_may_belong_to(parent_type: str) -> bool:
    """Utility must not sit as a peer of main Tools directly under Product."""
    return parent_type in {
        "Tool",
        "Service",
        "Procedure",
        "FunctionArea",
        "Module",
        "Feature",
    }
