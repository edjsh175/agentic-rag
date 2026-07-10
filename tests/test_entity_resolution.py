from dataclasses import dataclass

from rag_knowledge.services.graph_extraction import EntityCandidate
from rag_knowledge.services.entity_resolution import EntityResolutionService


@dataclass
class FakeDB:
    entities: list[dict]
    aliases: list[dict]

    def list_entities(self):
        return self.entities

    def list_aliases(self):
        return self.aliases


def test_resolution_reuses_same_name_and_type():
    service = EntityResolutionService(FakeDB([{"id": "e1", "name": "Apache", "entity_type": "EnvironmentComponent"}], []))
    result = service.resolve(EntityCandidate(" Apache ", "EnvironmentComponent"))
    assert result.action == "reuse"
    assert result.target_id == "e1"
    assert result.diagnostics == []


def test_resolution_reports_same_name_type_conflict():
    service = EntityResolutionService(FakeDB([{"id": "e1", "name": "Apache", "entity_type": "Service"}], []))
    result = service.resolve(EntityCandidate("Apache", "EnvironmentComponent"))
    assert result.action == "diagnostic"
    assert result.diagnostics[0].code == "type_conflict"


def test_resolution_reports_alias_and_possible_duplicate():
    service = EntityResolutionService(
        FakeDB(
            [{"id": "e1", "name": "PostgreSQL", "entity_type": "EnvironmentComponent"}],
            [{"entity_id": "e1", "alias": "Postgres"}],
        )
    )
    alias = service.resolve(EntityCandidate("Postgres", "EnvironmentComponent"))
    duplicate = service.resolve(EntityCandidate("PostgreSQL database", "EnvironmentComponent"))
    assert alias.action == "alias"
    assert alias.target_id == "e1"
    assert duplicate.diagnostics[0].code == "possible_duplicate"
