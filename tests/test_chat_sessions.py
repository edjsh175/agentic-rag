import json
import pytest
from pathlib import Path
from rag_knowledge.services.chat_storage import ChatStorage


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
    storage.save_session(fp, s1_id, messages, title="初次问候")

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
