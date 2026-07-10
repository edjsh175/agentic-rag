# -*- coding: utf-8 -*-
"""Task 8.1 graph fact gate: PASS / NEEDS_APPLY / BLOCKED."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from rag_knowledge.models.graph_schema import make_field_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_intent_scoring import GraphIntentFactProvider
from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

PROFILE_IDS = (
    "pipeline_point_table",
    "pipeline_line_table",
    "pipeline_face_table",
    "dom_builder_publish",
)

POLICY_ENTITY_REFS = {
    "pipeline_point_table": "管线点表",
    "pipeline_line_table": "管线线表",
    "pipeline_face_table": "管线面表",
    "dom_builder_publish": "DOMBuilder",
}

EXPECTED_ALIASES = {
    "管线点表": ("点数据结构",),
    "管线线表": ("线表数据结构",),
    "管线面表": ("面表数据结构",),
}

EXPECTED_FIELDS = {
    "管线点表": ("管点编号", "地面高程"),
    "管线线表": ("管线编号",),
    "管线面表": ("管面编号",),
}

EXPECTED_SIBLINGS = {
    "管线点表": {"管线线表", "管线面表"},
    "管线线表": {"管线点表", "管线面表"},
    "管线面表": {"管线点表", "管线线表"},
}

BLOCKING_DIAGNOSTIC_CODES = frozenset({
    "pending_only_entity",
    "pending_only_alias",
    "pending_only_relation",
    "rejected_relation_exists",
    "ambiguous_section_entity",
})

ALLOWED_DIAGNOSTIC_CODES = BLOCKING_DIAGNOSTIC_CODES | frozenset({
    "generic_recall_term",
    "missing_section_entity",
    "missing_approved_entity",
    "missing_approved_alias",
    "missing_approved_relation",
})


@dataclass
class Task81GraphGateReport:
    verdict: str
    issues: list[str] = field(default_factory=list)
    runtime_facts: dict = field(default_factory=dict)
    preview_summary: dict = field(default_factory=dict)
    global_graph_quality: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "issues": self.issues,
            "runtime_facts": self.runtime_facts,
            "preview_summary": self.preview_summary,
            "global_graph_quality": self.global_graph_quality,
        }


class Task81GraphGateValidator:
    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def validate(self, *, include_global_quality: bool = True) -> Task81GraphGateReport:
        issues: list[str] = []
        provider = GraphIntentFactProvider(self.db)
        entity_refs = [POLICY_ENTITY_REFS[pid] for pid in PROFILE_IDS]
        facts_map = provider.load_many(entity_refs)

        runtime_facts = {}
        for pid in PROFILE_IDS:
            ref = POLICY_ENTITY_REFS[pid]
            facts = facts_map.get(ref)
            runtime_facts[pid] = {
                "loaded": facts is not None,
                "aliases": list(facts.aliases) if facts else [],
                "section_paths": list(facts.section_paths) if facts else [],
                "field_names": list(facts.field_names) if facts else [],
                "sibling_names": list(facts.sibling_names) if facts else [],
            }
            if facts is None:
                issues.append(f"runtime: cannot load approved facts for {ref} ({pid})")
                continue
            for alias in EXPECTED_ALIASES.get(ref, ()):
                if alias not in facts.aliases:
                    issues.append(f"runtime: missing approved alias {ref} -> {alias}")
            for leaf in EXPECTED_FIELDS.get(ref, ()):
                scoped = make_field_entity_name(ref, leaf)
                if scoped not in facts.field_names and leaf not in facts.field_names:
                    issues.append(f"runtime: missing has_field {ref} -> {scoped}")
            expected_sibs = EXPECTED_SIBLINGS.get(ref, set())
            missing_sibs = expected_sibs - set(facts.sibling_names)
            if missing_sibs:
                issues.append(f"runtime: missing different_from siblings for {ref}: {sorted(missing_sibs)}")

        preview = ProfileGraphSyncService(db=self.db).preview()
        preview_summary: dict[str, dict] = {}
        actionable_total = 0
        blocking_diagnostics: list[str] = []

        for profile_set in preview.profiles:
            counts = {
                "entities": len(profile_set.entities),
                "aliases": len(profile_set.aliases),
                "relations": len(profile_set.relations),
                "weak_relations": len(profile_set.weak_relations),
                "diagnostics": len(profile_set.diagnostics),
            }
            actionable = counts["entities"] + counts["aliases"] + counts["relations"] + counts["weak_relations"]
            actionable_total += actionable
            preview_summary[profile_set.profile_id] = counts

            for diagnostic in profile_set.diagnostics:
                code = diagnostic.code
                if code in BLOCKING_DIAGNOSTIC_CODES:
                    blocking_diagnostics.append(f"{profile_set.profile_id}: {code}: {diagnostic.message}")
                elif code not in ALLOWED_DIAGNOSTIC_CODES:
                    issues.append(f"preview: unexpected diagnostic {profile_set.profile_id}: {code}")

            for entity in profile_set.entities:
                if entity.entity_type == "Section" and ">" in entity.name and "::" not in entity.name:
                    issues.append(f"preview: bare section entity candidate: {entity.name}")
                if entity.entity_type == "Field" and "." not in entity.name:
                    issues.append(f"preview: unscoped field entity candidate: {entity.name}")

        if blocking_diagnostics:
            issues.extend(blocking_diagnostics)

        global_graph_quality: dict = {}
        if include_global_quality:
            try:
                from rag_knowledge.services.graph_extraction import GraphQualityService

                report = GraphQualityService(self.db).inspect_graph(profile="full")
                global_graph_quality = {
                    "ok": report.ok,
                    "errors": list(report.errors),
                    "warnings": list(report.warnings),
                    "stats": dict(report.stats),
                }
            except Exception as exc:  # pragma: no cover - quality optional in tests
                global_graph_quality = {"ok": False, "errors": [str(exc)], "warnings": [], "stats": {}}

        verdict = self._decide_verdict(issues, actionable_total, blocking_diagnostics, facts_map)
        return Task81GraphGateReport(
            verdict=verdict,
            issues=issues,
            runtime_facts=runtime_facts,
            preview_summary=preview_summary,
            global_graph_quality=global_graph_quality,
        )

    @staticmethod
    def _decide_verdict(
        issues: Iterable[str],
        actionable_total: int,
        blocking_diagnostics: list[str],
        facts_map: dict,
    ) -> str:
        issue_list = list(issues)
        if blocking_diagnostics:
            return "BLOCKED"
        if any(issue.startswith("preview: bare section") or issue.startswith("preview: unscoped field") for issue in issue_list):
            return "BLOCKED"
        runtime_issues = [issue for issue in issue_list if issue.startswith("runtime:")]
        if not runtime_issues and actionable_total == 0:
            return "PASS"
        if runtime_issues and actionable_total > 0 and not blocking_diagnostics:
            return "NEEDS_APPLY"
        if runtime_issues and actionable_total == 0:
            return "BLOCKED"
        if actionable_total > 0:
            return "NEEDS_APPLY"
        return "BLOCKED"
