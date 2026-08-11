# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.services.graph_extraction.server_leaf_extractor import ServerLeafExtractor


def test_server_leaf_redis_procedure_and_command():
    chunk = {
        "chunk_id": "sv1",
        "content": (
            "Redis安装\n"
            "安装 Redis 服务后，执行下列命令开启自启：\n"
            "systemctl enable redis\n"
            "systemctl start redis\n"
            "端口：6379\n"
        ),
        "metadata": {
            "source": "Stamp服务部署.docx",
            "doc_category": "StampServer",
            "section_path": "服务部署 > Redis安装",
        },
    }
    result = ServerLeafExtractor().extract(chunk)
    assert result.entity("Redis安装") is not None
    assert result.entity("Redis安装").entity_type == "Procedure"
    assert result.entity("systemctl enable redis") is not None
    assert result.entity("systemctl enable redis").entity_type == "Command"
    assert result.has_relation("Redis安装", "runs_command", "systemctl enable redis")
    # No Service in path → no invent Service→Product; no ConfigItem for 端口.
    assert result.entity("端口") is None
    assert not any(e.entity_type == "ConfigItem" for e in result.entities)
    assert not any(r.relation_type == "belongs_to" and r.target_name == "StampServer" for r in result.relations)


def test_server_leaf_attaches_has_procedure_to_service_owner():
    chunk = {
        "chunk_id": "sv2",
        "content": "管线发布服务安装\n配置 se_pipeline_publish.so 模块。\n",
        "metadata": {
            "source": "Stamp服务部署.docx",
            "doc_category": "StampServer",
            "section_path": "服务部署 > 管线发布服务 > 管线发布服务安装",
        },
    }
    result = ServerLeafExtractor().extract(chunk)
    assert result.entity("管线发布服务安装") is not None
    assert result.entity("管线发布服务安装").entity_type == "Procedure"
    assert result.has_relation("管线发布服务", "has_procedure", "管线发布服务安装")
    assert not any(
        r.relation_type == "belongs_to" and r.target_name == "StampServer"
        for r in result.relations
    )


def test_server_leaf_skips_non_server_category():
    chunk = {
        "chunk_id": "sv3",
        "content": "systemctl enable redis\n",
        "metadata": {
            "doc_category": "StampTools",
            "section_path": "服务部署 > Redis安装",
        },
    }
    result = ServerLeafExtractor().extract(chunk)
    assert result.entities == []
    assert result.relations == []


def test_server_leaf_requires_deploy_path():
    chunk = {
        "chunk_id": "sv4",
        "content": "systemctl enable redis\n",
        "metadata": {
            "doc_category": "StampServer",
            "section_path": "概述 > 简介",
        },
    }
    result = ServerLeafExtractor().extract(chunk)
    assert result.entities == []
