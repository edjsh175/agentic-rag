import json
import pytest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_knowledge.services.chat_storage import ChatStorage
from rag_knowledge.api import routes


def test_chat_storage_crud_and_migration(tmp_path: Path):
    storage = ChatStorage(tmp_path)
    fp = "test_fp_001"

    # 1. 初始状态为空
    sessions_data = storage.list_sessions(fp)
    assert sessions_data["active_session_id"] is None
    assert sessions_data["sessions"] == []

    # 2. 创建新会话
    s1 = storage.create_session(fp, title="对话一")
    assert s1["title"] == "对话一"
    s1_id = s1["id"]

    # 3. 再次获取列表
    sessions_data = storage.list_sessions(fp)
    assert sessions_data["active_session_id"] == s1_id
    assert len(sessions_data["sessions"]) == 1
    assert sessions_data["sessions"][0]["id"] == s1_id
    assert sessions_data["sessions"][0]["message_count"] == 0

    # 4. 保存会话消息
    messages = [
        {"id": "1", "role": "user", "content": "你好"},
        {"id": "2", "role": "assistant", "content": "你好！有什么可以帮你的？"},
    ]
    saved = storage.save_session(fp, s1_id, messages, title="初次问候")
    assert saved is not None
    assert saved["title"] == "初次问候"

    session_detail = storage.get_session(fp, s1_id)
    assert session_detail is not None
    assert session_detail["title"] == "初次问候"
    assert len(session_detail["messages"]) == 2

    # 5. 创建第二个会话
    s2 = storage.create_session(fp, title="对话二")
    s2_id = s2["id"]
    sessions_data = storage.list_sessions(fp)
    assert len(sessions_data["sessions"]) == 2
    assert sessions_data["active_session_id"] == s2_id

    # 6. 重命名会话
    ok = storage.rename_session(fp, s2_id, "重命名后的对话二")
    assert ok is True
    assert storage.get_session(fp, s2_id)["title"] == "重命名后的对话二"

    # 7. 切换活跃会话
    storage.set_active_session(fp, s1_id)
    assert storage.list_sessions(fp)["active_session_id"] == s1_id

    # 8. 删除会话
    deleted = storage.delete_session(fp, s1_id)
    assert deleted is True
    sessions_after_delete = storage.list_sessions(fp)
    assert len(sessions_after_delete["sessions"]) == 1
    assert sessions_after_delete["sessions"][0]["id"] == s2_id
    assert sessions_after_delete["active_session_id"] == s2_id


def test_save_non_existent_session_returns_none(tmp_path: Path):
    storage = ChatStorage(tmp_path)
    fp = "test_fp_strict"

    # 对不存在的会话执行 save_session，严格返回 None 且不自动创建
    res = storage.save_session(fp, "non_existent_sid", [{"id": "1", "role": "user", "content": "hello"}])
    assert res is None

    sessions_data = storage.list_sessions(fp)
    assert sessions_data["sessions"] == []
    assert sessions_data["active_session_id"] is None


def test_api_save_deleted_session_returns_404(tmp_path: Path, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    fp = "test_fp_api_resurrect"

    # 隔离测试路由中的 _chat_storage 路径
    test_storage = ChatStorage(tmp_path)
    monkeypatch.setattr(routes, "_chat_storage", test_storage)

    headers = {"X-Device-Fingerprint": fp}

    # 1. 创建会话
    resp = client.post("/chat/sessions", json={"title": "测试会话"}, headers=headers)
    assert resp.status_code == 200
    sid = resp.json()["id"]

    # 2. 删除该会话
    del_resp = client.delete(f"/chat/sessions/{sid}", headers=headers)
    assert del_resp.status_code == 200

    # 3. 模拟前端滞后的保存请求到达已删除的会话
    put_resp = client.put(
        f"/chat/sessions/{sid}",
        json={"messages": [{"id": "1", "role": "user", "content": "迟到的保存"}]},
        headers=headers,
    )
    assert put_resp.status_code == 404
    assert put_resp.json()["detail"] == "会话不存在"

    # 4. 再次获取列表，必须为空，不能被复活
    list_resp = client.get("/chat/sessions", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["sessions"] == []
    assert list_resp.json()["active_session_id"] is None

    # QA 调试追踪不能再作为会话恢复来源。
    sync_resp = client.post("/chat/sessions/sync-traces", headers=headers)
    assert sync_resp.status_code == 405


def test_active_session_persists_across_restart(tmp_path: Path):
    fp = "test_fp_restart"
    storage1 = ChatStorage(tmp_path)

    s1 = storage1.create_session(fp, title="S1")
    s2 = storage1.create_session(fp, title="S2")
    storage1.set_active_session(fp, s1["id"])
    assert storage1.list_sessions(fp)["active_session_id"] == s1["id"]

    # 模拟后端重启：新实例化 ChatStorage
    storage2 = ChatStorage(tmp_path)
    data2 = storage2.list_sessions(fp)
    assert len(data2["sessions"]) == 2
    assert data2["active_session_id"] == s1["id"]

    # 切换为 S2 并再次重启
    storage2.set_active_session(fp, s2["id"])
    storage3 = ChatStorage(tmp_path)
    data3 = storage3.list_sessions(fp)
    assert data3["active_session_id"] == s2["id"]


def test_create_session_idempotent(tmp_path: Path):
    fp = "test_fp_idempotent"
    storage = ChatStorage(tmp_path)

    s1 = storage.create_session(fp, title="初始标题", session_id="fixed_sid_123")
    assert s1["id"] == "fixed_sid_123"

    # 再次使用相同的 session_id 调用创建
    s2 = storage.create_session(fp, title="新标题", session_id="fixed_sid_123")
    assert s2["id"] == "fixed_sid_123"

    # 验证列表中仅存在一条记录
    data = storage.list_sessions(fp)
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["id"] == "fixed_sid_123"


def test_concurrent_chat_storage_writes(tmp_path: Path):
    fp = "test_fp_concurrency"
    storage = ChatStorage(tmp_path)
    s = storage.create_session(fp, title="并发测试会话")
    sid = s["id"]

    def worker(i: int):
        storage.save_session(
            fp,
            sid,
            [{"id": f"msg_{i}", "role": "user", "content": f"并发测试内容 {i}"}],
            title=f"并发标题 {i}",
        )
        storage.set_active_session(fp, sid)
        storage.list_sessions(fp)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, i) for i in range(30)]
        for f in futures:
            f.result()

    final_data = storage.list_sessions(fp)
    assert len(final_data["sessions"]) == 1
    detail = storage.get_session(fp, sid)
    assert detail is not None
    assert len(detail["messages"]) == 1


def test_legacy_format_migration(tmp_path: Path):
    chats_dir = tmp_path / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)
    fp = "legacy_fp"

    # 模拟旧版本单会话 JSON 文件
    legacy_data = {
        "fingerprint": fp,
        "updated_at": "2026-08-01T10:00:00",
        "messages": [
            {"id": "m1", "role": "user", "content": "如何设计 RAG 架构？"},
            {"id": "m2", "role": "assistant", "content": "RAG 架构通常包含..."},
        ],
    }
    (chats_dir / f"{fp}.json").write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

    storage = ChatStorage(tmp_path)
    sessions_data = storage.list_sessions(fp)
    assert len(sessions_data["sessions"]) == 1
    s = sessions_data["sessions"][0]
    assert s["message_count"] == 2
    assert "如何设计 RAG 架构" in s["title"]
    assert sessions_data["active_session_id"] == s["id"]

    # 验证旧接口兼容性
    loaded = storage.load(fp)
    assert loaded is not None
    assert len(loaded["messages"]) == 2
