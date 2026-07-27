# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
import httpx

from rag_knowledge.config import Config
from rag_knowledge.services.graph_extraction.llm_extractor import (
    LLMGraphExtractor,
    normalize_name
)


def test_normalize_name():
    assert normalize_name("  Test  Name  ") == "Test Name"
    assert normalize_name("组件（中文）") == "组件(中文)"


def test_llm_graph_extractor_validation(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    
    # Configure min_confidence = 0.60
    cfg.graph_extraction_llm.min_confidence = 0.60

    extractor = LLMGraphExtractor()

    # Test JSON input with various valid and invalid candidates
    test_json = {
        "entities": [
            # Valid entity
            {
                "name": "PostgreSQL  数据库",
                "entity_type": "EnvironmentComponent",
                "confidence": 0.90,
                "evidence_text": "PostgreSQL 16 安装"
            },
            # Invalid type
            {
                "name": "InvalidTypeEntity",
                "entity_type": "NotAllowedType",
                "confidence": 0.90,
                "evidence_text": "Evidence"
            },
            # Missing evidence
            {
                "name": "NoEvidenceEntity",
                "entity_type": "Tool",
                "confidence": 0.85,
                "evidence_text": ""
            },
            # Low confidence
            {
                "name": "LowConfidenceEntity",
                "entity_type": "Service",
                "confidence": 0.40,
                "evidence_text": "PostgreSQL 16 安装"
            },
            # Missing confidence
            {
                "name": "MissingConfEntity",
                "entity_type": "Tool",
                "evidence_text": "PostgreSQL 16 安装"
            },
            # Confidence out of range
            {
                "name": "RangeConfEntity",
                "entity_type": "Tool",
                "confidence": 1.5,
                "evidence_text": "PostgreSQL 16 安装"
            },
            # Invalid evidence text
            {
                "name": "BadEvidenceEntity",
                "entity_type": "Tool",
                "confidence": 0.90,
                "evidence_text": "Not matching anything"
            }
        ],
        "relations": [
            # Valid relation
            {
                "source_name": "管线发布服务",
                "relation_type": "depends_on",
                "target_name": "PostgreSQL 数据库",
                "confidence": 0.80,
                "evidence_text": "该服务依赖 PostgreSQL"
            },
            # Invalid relation type
            {
                "source_name": "管线发布服务",
                "relation_type": "invalid_rel",
                "target_name": "PostgreSQL",
                "confidence": 0.80,
                "evidence_text": "该服务依赖 PostgreSQL"
            }
        ],
        "aliases": [
            # Valid alias
            {
                "entity_name": "PostgreSQL 数据库",
                "alias": "Postgres",
                "confidence": 0.95,
                "evidence_text": "或称为 Postgres"
            }
        ],
        "diagnostics": [
            {
                "code": "custom_diagnostic",
                "message": "warning message"
            }
        ]
    }

    # Mock _call_llm_with_retries to return raw json
    with patch.object(extractor, "_call_llm_with_retries", return_value=json.dumps(test_json)):
        chunk = {
            "chunk_id": "chunk-1",
            "content": "PostgreSQL 16 安装, 或称为 Postgres. 该服务依赖 PostgreSQL.",
            "metadata": {
                "doc_category": "StampServer",
                "section_path": "安装部署 > DB"
            }
        }
        res = extractor.extract(chunk)

        # 1. Assert entity validation/normalization
        assert len(res.entities) == 1
        assert res.entities[0].name == "PostgreSQL 数据库"
        assert res.entities[0].entity_type == "EnvironmentComponent"
        assert res.entities[0].properties["confidence"] == 0.90
        assert res.entities[0].properties["created_by"] == "llm:schema_extractor"
        assert res.entities[0].evidence_text == "PostgreSQL 16 安装"

        # 2. Assert relation validation/normalization
        assert len(res.relations) == 1
        assert res.relations[0].source_name == "管线发布服务"
        assert res.relations[0].relation_type == "depends_on"
        assert res.relations[0].target_name == "PostgreSQL 数据库"

        # 3. Assert aliases validation
        assert hasattr(res, "aliases")
        assert len(res.aliases) == 1
        assert res.aliases[0]["entity_name"] == "PostgreSQL 数据库"
        assert res.aliases[0]["alias"] == "Postgres"

        # 4. Assert diagnostics captured correctly
        diagnostic_codes = {d.code for d in res.diagnostics}
        assert "invalid_entity_type" in diagnostic_codes
        assert "missing_evidence" in diagnostic_codes
        assert "low_confidence" in diagnostic_codes
        assert "invalid_relation_type" in diagnostic_codes
        assert "custom_diagnostic" in diagnostic_codes
        assert "missing_confidence" in diagnostic_codes
        assert "confidence_out_of_range" in diagnostic_codes
        assert "invalid_evidence_text" in diagnostic_codes


def test_llm_graph_extractor_http_call(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    cfg.graph_extraction_llm.provider = "ollama"
    cfg.graph_extraction_llm.model = "test-model"

    extractor = LLMGraphExtractor()

    # Mock httpx Client post response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "content": '{"entities": [], "relations": []}'
        }
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        chunk = {
            "chunk_id": "chunk-1",
            "content": "Test content",
            "metadata": {
                "doc_category": "StampServer",
                "section_path": "Root"
            }
        }
        res = extractor.extract(chunk)

        assert len(res.entities) == 0
        assert len(res.relations) == 0
        assert len(res.diagnostics) == 0

        # Assert correct url and payload used
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert cfg.ollama_base_url in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["model"] == "test-model"
        assert payload["messages"][0]["role"] == "user"
        # qwen3 thinking mode must be disabled for stable JSON graph extraction
        assert payload.get("think") is False
        assert payload.get("format") == "json"


def test_llm_graph_extractor_failure_handled(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    extractor = LLMGraphExtractor()

    # Mock httpx to throw exception (e.g. server offline)
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("Ollama connection failed")):
        chunk = {
            "chunk_id": "chunk-1",
            "content": "Test content",
            "metadata": {}
        }
        res = extractor.extract(chunk)

        # Extraction should not crash, but output a diagnostic
        assert len(res.entities) == 0
        assert len(res.relations) == 0
        assert len(res.diagnostics) == 1
        assert res.diagnostics[0].code == "llm_extraction_failed"
        assert "Ollama connection failed" in res.diagnostics[0].message


def test_noisy_config_item_and_command_reclassify():
    from rag_knowledge.services.graph_extraction.llm_extractor import (
        is_noisy_config_item,
        maybe_reclassify_as_command,
    )

    assert is_noisy_config_item("JPG")
    assert is_noisy_config_item("WebP")
    assert is_noisy_config_item("WGS-84")
    assert is_noisy_config_item("国家2000")
    assert is_noisy_config_item("高斯投影（本地）")
    assert is_noisy_config_item("东向偏移")
    assert is_noisy_config_item("渲染效率")
    assert is_noisy_config_item("EPSG:4326")
    assert is_noisy_config_item("四参数")
    assert is_noisy_config_item("经纬度坐标")
    assert not is_noisy_config_item("PipelinePublishConfig")
    assert not is_noisy_config_item("nginx.conf")

    assert maybe_reclassify_as_command("Procedure", "systemctl restart redis") == "Command"
    assert maybe_reclassify_as_command("Step", "yum install nginx") == "Command"
    assert maybe_reclassify_as_command("ConfigItem", "tar -zxvf app.tar.gz") == "Command"
    assert maybe_reclassify_as_command("Procedure", "坐标偏移") == "Procedure"

    from rag_knowledge.services.graph_extraction.llm_extractor import (
        chunk_has_command_signal,
        early_check_relation_endpoints,
    )

    # Early illegal pair reject / direction flip when both types known
    idx = {"开挖数据源": "Procedure", "图层透明度": "ConfigItem", "systemctl restart redis": "Command", "安装脚本": "Step"}
    src, tgt, flipped, reason = early_check_relation_endpoints(
        "开挖数据源", "has_step", "图层透明度", idx
    )
    assert reason
    assert not flipped

    src, tgt, flipped, reason = early_check_relation_endpoints(
        "systemctl restart redis", "runs_command", "安装脚本", idx
    )
    assert reason is None
    assert flipped
    assert src == "安装脚本" and tgt == "systemctl restart redis"

    src, tgt, flipped, reason = early_check_relation_endpoints(
        "未知源", "has_step", "图层透明度", idx
    )
    assert reason is None and not flipped  # unknown endpoint → defer

    assert chunk_has_command_signal("run:\nsystemctl restart redis\n")

    assert chunk_has_command_signal("执行\nsystemctl restart redis\n完成")
    assert chunk_has_command_signal("$ yum install nginx")
    assert not chunk_has_command_signal("PipelineBuilder 工程设置与坐标偏移")


def test_llm_extractor_rejects_illegal_relation_pair_early(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    cfg.graph_extraction_llm.min_confidence = 0.60
    extractor = LLMGraphExtractor()
    content = "开挖数据源支持自定义开挖；图层透明度在配置面板。"
    payload = {
        "entities": [
            {
                "name": "开挖数据源",
                "entity_type": "Procedure",
                "confidence": 0.9,
                "evidence_text": "开挖数据源支持自定义开挖",
            },
            {
                "name": "图层透明度",
                "entity_type": "ConfigItem",
                "confidence": 0.9,
                "evidence_text": "图层透明度在配置面板",
            },
            {
                "name": "自定义开挖",
                "entity_type": "Step",
                "confidence": 0.9,
                "evidence_text": "开挖数据源支持自定义开挖",
            },
        ],
        "relations": [
            {
                "source_name": "开挖数据源",
                "relation_type": "has_step",
                "target_name": "图层透明度",
                "confidence": 0.9,
                "evidence_text": "图层透明度在配置面板",
            },
            {
                "source_name": "开挖数据源",
                "relation_type": "has_step",
                "target_name": "自定义开挖",
                "confidence": 0.9,
                "evidence_text": "开挖数据源支持自定义开挖",
            },
        ],
        "aliases": [],
        "diagnostics": [],
    }
    with patch.object(extractor, "_call_llm_with_retries", return_value=json.dumps(payload)):
        res = extractor.extract(
            {
                "chunk_id": "c1",
                "content": content,
                "metadata": {"doc_category": "StampWebRTC", "section_path": "分析"},
            }
        )
    assert len(res.relations) == 1
    assert res.relations[0].target_name == "自定义开挖"
    codes = {d.code for d in res.diagnostics}
    assert "illegal_relation_pair" in codes


def test_llm_graph_extractor_filters_noisy_config_and_promotes_command(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    cfg.graph_extraction_llm.min_confidence = 0.60
    cfg.graph_extraction_llm.prompt_version = "v2"
    extractor = LLMGraphExtractor()

    payload = {
        "entities": [
            {
                "name": "JPG",
                "entity_type": "ConfigItem",
                "confidence": 0.95,
                "evidence_text": "纹理格式必须选择JPG",
            },
            {
                "name": "PipelinePublishConfig",
                "entity_type": "ConfigItem",
                "confidence": 0.92,
                "evidence_text": "编辑 PipelinePublishConfig",
            },
            {
                "name": "systemctl restart redis",
                "entity_type": "Procedure",
                "confidence": 0.88,
                "evidence_text": "执行 systemctl restart redis",
            },
        ],
        "relations": [],
        "aliases": [],
        "diagnostics": [],
    }
    content = "纹理格式必须选择JPG。编辑 PipelinePublishConfig。执行 systemctl restart redis"
    with patch.object(extractor, "_call_llm_with_retries", return_value=json.dumps(payload)):
        res = extractor.extract(
            {
                "chunk_id": "c1",
                "content": content,
                "metadata": {"doc_category": "StampServer", "section_path": "部署"},
            }
        )

    names = {e.name: e.entity_type for e in res.entities}
    assert "JPG" not in names
    assert names.get("PipelinePublishConfig") == "ConfigItem"
    assert names.get("systemctl restart redis") == "Command"
    assert any(d.code == "noisy_config_item" for d in res.diagnostics)


def test_prompt_v2_template_loaded(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    cfg.graph_extraction_llm.prompt_version = "v2"
    extractor = LLMGraphExtractor()
    prompt = extractor.build_prompt(doc_category="StampTools", section_path="x", content="y")
    assert "ConfigItem anti-noise" in prompt
    assert "Command recall" in prompt
    assert "Do NOT extract as ConfigItem" in prompt


def test_prompt_v3_has_direction_few_shot(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    cfg.graph_extraction_llm.prompt_version = "v3"
    extractor = LLMGraphExtractor()
    prompt = extractor.build_prompt(doc_category="StampServer", section_path="x", content="y")
    assert "Relation direction (CRITICAL)" in prompt
    assert "Few-shot direction examples" in prompt
    assert "never Command → actor" in prompt or "never Command → Service" in prompt or "WRONG:" in prompt
