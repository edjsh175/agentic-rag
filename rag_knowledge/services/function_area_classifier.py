"""Classifier for identifying product capability sections vs. document organization sections."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClassifyContext:
    doc_category: str = ""
    source: str = ""
    parts: list[str] = field(default_factory=list)
    depth: int = 0


class FunctionAreaClassifier:
    """Classifies section_path segments into product capabilities vs doc structure."""

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

    def classify(self, section_name: str, context: ClassifyContext | None = None) -> str:
        """Classify a section segment name.

        Returns:
            'function_area': Product capability section that should be a FunctionArea node.
            'section': Pure document organization section.
            'ambiguous': Unknown / ambiguous section, defaults to conservative 'section'.
        """
        name = section_name.strip()
        if not name:
            return "section"

        # 1. Exact match doc organization
        if name in self.DOC_ORGANIZATION_PATTERNS:
            return "section"

        # 2. Exact match capability
        if name in self.CAPABILITY_PATTERNS:
            return "function_area"

        # 3. Ends with doc org keywords (e.g., xxx指南, xxx概述)
        if name.endswith(("指南", "概述", "简介", "说明", "教程", "前言", "附录", "目录", "常见问题")):
            return "section"

        # 4. Capability keyword pattern match
        if self.CAPABILITY_KEYWORD_RE.match(name):
            return "function_area"

        # 5. Default conservative fallback
        return "ambiguous"
