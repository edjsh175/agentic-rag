"""Classifier for identifying product capability sections vs. document organization sections."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassifyContext:
    doc_category: str = ""
    source: str = ""
    parts: list[str] = field(default_factory=list)
    depth: int = 0
    owner_name: str = ""


class FunctionAreaClassifier:
    """Classifies section_path segments into product capabilities vs doc structure.

    Three-level judgment strategy:
    Level 1 (Rule/Catalog): Match function_area_catalog.json seed dictionary or exact capability patterns.
    Level 2 (LLM Classifier): Ask LLM if a section represents a product capability (optional / optional LLM prompt).
    Level 3 (Fallback Section): Conservative default, treat ambiguous sections as pure Document Sections.
    """

    # Explicit document organization sections (should stay pure Sections, not FunctionArea)
    DOC_ORGANIZATION_PATTERNS: set[str] = {
        "快速开始", "快速入门", "入门指南", "使用指南", "操作指南",
        "目录", "附录", "参考", "前言", "概述", "简介", "关于",
        "版本历史", "更新日志", "版本说明", "发布说明",
        "示例教程", "教程", "示例", "常见问题", "注意事项",
    }

    # Explicit product capability sections (should become FunctionArea)
    CAPABILITY_PATTERNS: set[str] = {
        "数据管理", "数据规范", "数据处理", "数据配置", "数据检查", "数据映射",
        "工程设置", "高级设置", "基本设置", "系统设置", "参数设置", "环境配置",
        "服务部署", "部署配置", "服务配置", "服务管理", "授权管理",
        "坐标设置", "坐标系", "投影设置", "图层管理",
        "发布设置", "发布配置", "网络设置", "分析配置",
    }

    # Keywords that strongly imply product capability
    CAPABILITY_KEYWORD_RE = re.compile(
        r".*(?:设置|管理|配置|规范|部署|分析|处理|映射|编辑|查询|更新|索引|发布|转换)$"
    )

    def __init__(self, catalog_path: str | Path | None = None):
        self._catalog: dict[str, set[str]] = {}
        root = Path(__file__).resolve().parents[2]
        path = Path(catalog_path) if catalog_path else root / "data" / "function_area_catalog.json"
        self.load_catalog(path)

    def load_catalog(self, path: Path) -> None:
        if not path.exists():
            logger.debug("function_area_catalog not found at %s", path)
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for owner, areas in data.items():
                    if isinstance(areas, list):
                        self._catalog[owner] = {str(item).strip() for item in areas if str(item).strip()}
        except Exception as exc:
            logger.warning("failed to load function_area_catalog from %s: %s", path, exc)

    def is_in_catalog(self, name: str, owner_name: str = "") -> bool:
        """Check if section name exists in function_area_catalog."""
        name_clean = name.strip()
        if not name_clean:
            return False
        if owner_name and owner_name in self._catalog:
            if name_clean in self._catalog[owner_name]:
                return True
        # Global fallback check across all owners in catalog
        return any(name_clean in areas for areas in self._catalog.values())

    def classify(self, section_name: str, context: ClassifyContext | None = None) -> str:
        """Classify a section segment name into 'function_area', 'section', or 'ambiguous'.

        Level 1: Match catalog seed dictionary & rule patterns.
        Level 2: Fallback to LLM semantic check if configured (simulated/extended).
        Level 3: Conservative fallback to 'section'.
        """
        name = section_name.strip()
        if not name:
            return "section"

        owner_name = context.owner_name if context else ""

        # Level 1a: Seed catalog lookup (Level 1 Trust Assets)
        if self.is_in_catalog(name, owner_name):
            return "function_area"

        # Level 1b: Exact match doc organization
        if name in self.DOC_ORGANIZATION_PATTERNS:
            return "section"

        # Level 1c: Exact match capability
        if name in self.CAPABILITY_PATTERNS:
            return "function_area"

        # Level 1d: Ends with doc org keywords (e.g., xxx指南, xxx概述)
        if name.endswith(("指南", "概述", "简介", "说明", "教程", "前言", "附录", "目录", "常见问题")):
            return "section"

        # Level 1e: Capability keyword pattern match
        if self.CAPABILITY_KEYWORD_RE.match(name):
            return "function_area"

        # Level 3: Default conservative fallback to 'ambiguous' (treated as Section)
        return "ambiguous"
