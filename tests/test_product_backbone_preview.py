from __future__ import annotations

import json

import pytest

from rag_knowledge.services.product_backbone_preview import ProductBackbonePreviewService


def _preview_path(tmp_path):
    path = tmp_path / "preview.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entities": [
                    {"name": "StampGIS三维产品", "graph_type": "Product", "layer": "产品体系层"},
                    {"name": "ActiveX", "graph_type": "Module", "layer": "客户端与渲染层"},
                ],
                "relations": [
                    {"source": "ActiveX", "relation_type": "belongs_to", "target": "StampGIS三维产品"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_product_backbone_preview_returns_stable_graph_data(tmp_path):
    service = ProductBackbonePreviewService(path=_preview_path(tmp_path))
    graph = service.list_graph_data()

    assert [node.label for node in graph.nodes] == ["StampGIS三维产品", "ActiveX"]
    assert graph.nodes[0].id == service._entity_id("StampGIS三维产品")
    assert graph.edges[0].source == service._entity_id("ActiveX")
    assert graph.edges[0].target == service._entity_id("StampGIS三维产品")
    assert graph.edges[0].id == service._relation_id("ActiveX", "belongs_to", "StampGIS三维产品")
    assert json.loads(graph.nodes[1].properties_json or "{}")["layer"] == "客户端与渲染层"


def test_product_backbone_preview_rejects_missing_relation_endpoint(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entities": [{"name": "ActiveX", "graph_type": "Module"}],
                "relations": [{"source": "ActiveX", "relation_type": "belongs_to", "target": "Missing"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing product backbone relation endpoint"):
        ProductBackbonePreviewService(path=path).list_graph_data()


def test_product_backbone_seed_contains_required_preview_entities():
    graph = ProductBackbonePreviewService().list_graph_data()
    labels = {node.label for node in graph.nodes}

    assert {"ActiveX", "StampUE", "WebGL", "UEModelBuilder", "PipelineBuilder", "se_port.so"} <= labels
    service_nodes = [
        node for node in graph.nodes
        if json.loads(node.properties_json or "{}").get("subtype") == "StampServerService"
    ]
    service_libraries = [
        node for node in graph.nodes
        if json.loads(node.properties_json or "{}").get("subtype") == "ServiceLibrary"
    ]
    assert len(service_nodes) == 29
    assert len(service_libraries) == 29


def test_product_backbone_preview_creates_updates_and_deletes_entities(tmp_path):
    service = ProductBackbonePreviewService(path=_preview_path(tmp_path))

    created = service.create_entity({
        "name": "WebGL",
        "graph_type": "Module",
        "layer": "客户端与渲染层",
        "subtype": "RenderingSystem",
        "description": "browser renderer",
        "alias_candidates": "webgl_alias",
    })
    assert created.label == "WebGL"

    updated = service.update_entity(created.id, {
        "name": "WebGL Runtime",
        "graph_type": "Module",
        "layer": "客户端与渲染层",
        "subtype": "RenderingSystem",
    })
    assert updated.label == "WebGL Runtime"

    relation = service.create_relation({
        "source_id": updated.id,
        "target_id": service._entity_id("ActiveX"),
        "relation_type": "belongs_to",
        "evidence_text": "manual edit",
    })
    assert relation.evidence_text == "manual edit"

    service.delete_entity(updated.id)
    graph = service.list_graph_data()
    assert "WebGL Runtime" not in {node.label for node in graph.nodes}
    assert relation.id not in {edge.id for edge in graph.edges}


def test_product_backbone_preview_relation_crud_and_duplicate_handling(tmp_path):
    service = ProductBackbonePreviewService(path=_preview_path(tmp_path))
    source_id = service._entity_id("ActiveX")
    target_id = service.list_graph_data().nodes[0].id

    relation = service.create_relation({
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": "requires",
    })
    duplicate = service.create_relation({
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": "requires",
    })

    graph = service.list_graph_data()
    assert relation.id == duplicate.id
    assert [edge.id for edge in graph.edges].count(relation.id) == 1

    service.delete_relation(relation.id)
    graph = service.list_graph_data()
    assert relation.id not in {edge.id for edge in graph.edges}


def test_product_backbone_preview_rejects_invalid_edit_inputs(tmp_path):
    service = ProductBackbonePreviewService(path=_preview_path(tmp_path))

    with pytest.raises(ValueError, match="duplicate product backbone entity"):
        service.create_entity({"name": "ActiveX", "graph_type": "Module"})

    with pytest.raises(KeyError, match="product backbone entity not found"):
        service.create_relation({
            "source_id": "missing",
            "target_id": service._entity_id("ActiveX"),
            "relation_type": "belongs_to",
        })

    with pytest.raises(ValueError, match="relation type cannot be empty"):
        service.create_relation({
            "source_id": service._entity_id("ActiveX"),
            "target_id": service.list_graph_data().nodes[0].id,
            "relation_type": "",
        })
