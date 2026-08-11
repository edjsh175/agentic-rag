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


def test_server_leaf_blocks_ancestor_apache_fan_in_on_auth_service():
    """授权服务 leaf: body titles must not invent Procedure (path-leaf-only)."""
    chunk = {
        "chunk_id": "sv5",
        "content": (
            "Apache服务配置\n"
            "某某安装\n"
            "授权服务用于校验客户端 License。\n"
            "sudo systemctl enable license-server\n"
        ),
        "metadata": {
            "source": "Stamp服务部署.docx",
            "doc_category": "StampServer",
            "section_path": "Apache服务配置 > 服务部署 > 授权服务",
        },
    }
    result = ServerLeafExtractor().extract(chunk)
    assert result.entity("Apache服务配置") is None
    assert result.entity("某某安装") is None
    assert result.entity("服务部署") is None
    assert not any(e.entity_type == "Procedure" for e in result.entities)
    assert not any(r.relation_type == "has_procedure" for r in result.relations)
    # Commands may still be extracted; without a procedure they are unowned leaves.
    assert result.entity("sudo systemctl enable license-server") is not None


def test_server_leaf_httpd_leaf_title_still_emits_procedure():
    chunk = {
        "chunk_id": "sv6",
        "content": (
            "Apache服务配置\n"
            "额外安装\n"
            "HTTPD服务配置完成后执行：\n"
            "systemctl enable httpd\n"
        ),
        "metadata": {
            "source": "Stamp服务部署.docx",
            "doc_category": "StampServer",
            "section_path": "Apache服务配置 > 服务部署 > HTTPD服务配置",
        },
    }
    result = ServerLeafExtractor().extract(chunk)
    assert result.entity("HTTPD服务配置") is not None
    assert result.entity("HTTPD服务配置").entity_type == "Procedure"
    assert result.entity("Apache服务配置") is None
    assert result.entity("额外安装") is None
    assert result.has_relation("HTTPD服务配置", "runs_command", "systemctl enable httpd")
    assert [e.name for e in result.entities if e.entity_type == "Procedure"] == ["HTTPD服务配置"]
