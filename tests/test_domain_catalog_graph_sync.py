import json

import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.domain_catalog_graph_sync import DomainCatalogGraphSyncService, SEED_CREATED_BY


def _catalog_path(tmp_path):
    catalog_path = tmp_path / "domain_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [{"name": "StampTools", "aliases": [], "doc_categories": ["StampTools"]}],
                "tools": [
                    {
                        "name": "PipelineBuilder",
                        "aliases": ["管线发布工具"],
                        "belongs_to": "StampTools",
                        "different_from": ["管线发布服务", "PipelinePublishConfig"],
                    }
                ],
                "services": [
                    {"name": "管线发布服务", "aliases": [], "belongs_to": "StampServer"},
                ],
                "environment_components": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return catalog_path


def test_catalog_loader_exposes_structured_seeds(tmp_path):
    catalog = DomainCatalogLoader(_catalog_path(tmp_path))
    pipeline = next(item for item in catalog.seeds() if item.name == "PipelineBuilder")

    assert pipeline.aliases == ["管线发布工具"]
    assert pipeline.belongs_to == "StampTools"
    assert pipeline.different_from == ["管线发布服务", "PipelinePublishConfig"]


def test_catalog_loader_rejects_invalid_different_from(tmp_path):
    catalog_path = tmp_path / "domain_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [],
                "tools": [{"name": "DemoTool", "aliases": [], "different_from": "bad"}],
                "services": [],
                "environment_components": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="different_from"):
        DomainCatalogLoader(catalog_path)


def test_domain_catalog_graph_sync_preview_stages_seed_facts(tmp_path):
    catalog = DomainCatalogLoader(_catalog_path(tmp_path))
    preview = DomainCatalogGraphSyncService(catalog=catalog).preview()

    assert any(item["name"] == "PipelineBuilder" for item in preview["entities"])
    assert any(item["alias"] == "管线发布工具" for item in preview["aliases"])
    assert any(
        item["relation_type"] == "different_from" and item["target_name"] == "管线发布服务"
        for item in preview["relations"]
    )


def test_domain_catalog_graph_sync_build_batch_creates_pending_candidates(isolated_storage, tmp_path):
    isolated_storage(db_name="catalog-sync.db", data_dir_name="catalog-sync-data", chroma_name="catalog-sync-chroma")
    db = RelationalDB()
    catalog = DomainCatalogLoader(_catalog_path(tmp_path))

    result = DomainCatalogGraphSyncService(db=db, catalog=catalog).build_batch(review_status="pending")

    candidates = db.list_extraction_candidates(result.batch_id)
    assert all(item["status"] == "pending" for item in candidates)
    assert result.stats["entity"] >= 1
    assert result.stats["alias"] >= 1
    assert result.stats["relation"] >= 2
    assert all(item["payload"].get("created_by") == SEED_CREATED_BY for item in candidates)
    assert db.get_entity_by_name("PipelineBuilder") is None
    assert not any(item["alias"] == "管线发布工具" for item in db.list_aliases())


def test_domain_catalog_seed_batch_can_be_applied_after_review(isolated_storage, tmp_path):
    isolated_storage(db_name="catalog-apply.db", data_dir_name="catalog-apply-data", chroma_name="catalog-apply-chroma")
    db = RelationalDB()
    catalog = DomainCatalogLoader(_catalog_path(tmp_path))
    db.create_entity("StampTools", "Product", "StampTools")
    db.create_entity("StampServer", "Product", "StampServer")
    db.create_entity("管线发布服务", "Service", "StampServer")
    db.create_entity("PipelinePublishConfig", "ConfigItem", "StampServer")

    batch = DomainCatalogGraphSyncService(db=db, catalog=catalog).build_batch(review_status="pending")
    ids = [item["id"] for item in db.list_extraction_candidates(batch.batch_id)]
    db.review_extraction_candidates(batch.batch_id, ids, "approved")
    db.set_extraction_batch_status(batch.batch_id, "approved")

    from rag_knowledge.services.graph_extraction import GraphCandidateApplier

    GraphCandidateApplier(db).apply(batch.batch_id)

    pipeline = db.get_entity_by_name("PipelineBuilder")
    assert pipeline is not None
    assert any(item["alias"] == "管线发布工具" for item in db.list_aliases(pipeline["id"]))
    assert any(
        item["relation_type"] == "different_from" and item["target_name"] == "管线发布服务"
        for item in db.list_relations(entity_id=pipeline["id"])
    )
