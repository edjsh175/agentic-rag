import json

from rag_knowledge.evaluation.dataset_health import check_eval_dataset_health


class FakeCollection:
    def __init__(self, chunks):
        self._chunks = chunks

    def get(self, ids=None, include=None):
        chunks = self._chunks
        if ids is not None:
            wanted = set(ids)
            chunks = [chunk for chunk in chunks if chunk["id"] in wanted]
        return {
            "ids": [chunk["id"] for chunk in chunks],
            "documents": [chunk["document"] for chunk in chunks],
            "metadatas": [chunk["metadata"] for chunk in chunks],
        }


def write_dataset(tmp_path, items):
    path = tmp_path / "eval_dataset.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return path


def test_health_passes_when_legacy_chunk_ids_exist(tmp_path):
    dataset_path = write_dataset(
        tmp_path,
        [{
            "question": "如何启动服务？",
            "relevant_chunk_ids": ["chunk-1"],
            "source": "manual.md",
        }],
    )
    collection = FakeCollection([
        {
            "id": "chunk-1",
            "document": "服务启动命令是 pm2 start app.js",
            "metadata": {"source": "manual.md", "section_path": "部署 > 启动"},
        }
    ])

    report = check_eval_dataset_health(dataset_path, collection=collection)

    assert report.status == "PASS"
    assert report.total_chunk_ids == 1
    assert report.existing_chunk_ids == 1
    assert report.chunk_health == 1.0
    assert report.invalid_questions == 0


def test_health_blocks_legacy_dataset_when_chunk_ids_are_stale(tmp_path):
    dataset_path = write_dataset(
        tmp_path,
        [{
            "question": "如何启动服务？",
            "relevant_chunk_ids": ["old-chunk"],
            "source": "manual.md",
        }],
    )
    collection = FakeCollection([
        {
            "id": "new-chunk",
            "document": "服务启动命令是 pm2 start app.js",
            "metadata": {"source": "manual.md", "section_path": "部署 > 启动"},
        }
    ])

    report = check_eval_dataset_health(dataset_path, collection=collection)

    assert report.status == "BLOCK"
    assert report.chunk_health == 0.0
    assert report.invalid_questions == 1
    assert report.missing_chunk_ids == ["old-chunk"]


def test_health_allows_stale_chunk_ids_when_expected_targets_match(tmp_path):
    dataset_path = write_dataset(
        tmp_path,
        [{
            "question": "如何启动服务？",
            "chunk_ids": ["old-chunk"],
            "expected_targets": [{
                "source": "manual.md",
                "section_path": "部署 > 启动",
                "keywords": ["pm2 start"],
            }],
        }],
    )
    collection = FakeCollection([
        {
            "id": "new-chunk",
            "document": "服务启动命令是 pm2 start app.js",
            "metadata": {"source": "manual.md", "section_path": "部署 > 启动"},
        }
    ])

    report = check_eval_dataset_health(dataset_path, collection=collection)

    assert report.status == "PASS"
    assert report.chunk_health == 0.0
    assert report.target_health == 1.0
    assert report.needs_chunk_id_refresh is True
    assert report.invalid_questions == 0
