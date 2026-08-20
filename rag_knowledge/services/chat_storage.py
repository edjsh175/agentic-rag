"""
聊天记录服务 —— 以 JSON 文件形式将多会话持久化到服务器

每个浏览器指纹对应一个 data/chats/{fingerprint}.json 文件，
内部维护 sessions 列表与 active_session_id。
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _generate_session_id() -> str:
    return f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"


class ChatStorage:
    """聊天记录多会话持久化"""

    def __init__(self, data_dir: Path):
        self._root = data_dir / "chats"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, fingerprint: str) -> Path:
        return self._root / f"{fingerprint}.json"

    def _load_raw(self, fingerprint: str) -> dict:
        """加载原始 JSON 并自动执行平滑迁移"""
        fp = self._path(fingerprint)
        now_str = datetime.now().isoformat(timespec="seconds")
        if not fp.exists():
            return {
                "fingerprint": fingerprint,
                "active_session_id": None,
                "updated_at": now_str,
                "sessions": [],
            }

        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON content is not a dict")
        except Exception as e:
            logger.warning("读取聊天记录失败 %s: %s，初始化为空会话", fingerprint, e)
            return {
                "fingerprint": fingerprint,
                "active_session_id": None,
                "updated_at": now_str,
                "sessions": [],
            }

        # 兼容旧版本单会话数据结构: {"fingerprint": "...", "updated_at": "...", "messages": [...]}
        if "sessions" not in data and "messages" in data:
            old_messages = data.get("messages") or []
            if old_messages:
                # 自动提取旧记录的第一条用户消息作为标题
                title = "历史对话"
                for m in old_messages:
                    if m.get("role") == "user" and m.get("content"):
                        title = str(m.get("content")).strip()[:24]
                        break
                default_id = _generate_session_id()
                data = {
                    "fingerprint": fingerprint,
                    "active_session_id": default_id,
                    "updated_at": data.get("updated_at") or now_str,
                    "sessions": [
                        {
                            "id": default_id,
                            "title": title,
                            "created_at": data.get("updated_at") or now_str,
                            "updated_at": data.get("updated_at") or now_str,
                            "messages": old_messages,
                        }
                    ],
                }
                # 写入迁移后的数据
                self._save_raw(fingerprint, data)
            else:
                data = {
                    "fingerprint": fingerprint,
                    "active_session_id": None,
                    "updated_at": now_str,
                    "sessions": [],
                }

        if "sessions" not in data:
            data["sessions"] = []
        if "active_session_id" not in data:
            data["active_session_id"] = data["sessions"][0]["id"] if data["sessions"] else None

        return data

    def _save_raw(self, fingerprint: str, data: dict) -> None:
        """保存数据到 JSON 文件"""
        fp = self._path(fingerprint)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_sessions(self, fingerprint: str) -> dict:
        """获取所有会话列表（摘要）及当前活跃会话 ID"""
        raw = self._load_raw(fingerprint)
        sessions_summary = []
        for s in raw.get("sessions", []):
            sessions_summary.append({
                "id": s.get("id"),
                "title": s.get("title") or "新建对话",
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "message_count": len(s.get("messages") or []),
            })
        return {
            "active_session_id": raw.get("active_session_id"),
            "sessions": sessions_summary,
        }

    def get_session(self, fingerprint: str, session_id: str) -> dict | None:
        """获取单个会话的完整消息数据"""
        raw = self._load_raw(fingerprint)
        for s in raw.get("sessions", []):
            if s.get("id") == session_id:
                return s
        return None

    def create_session(
        self,
        fingerprint: str,
        title: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """创建新会话"""
        raw = self._load_raw(fingerprint)
        sid = session_id or _generate_session_id()
        now_str = datetime.now().isoformat(timespec="seconds")
        new_sess = {
            "id": sid,
            "title": title or "新建对话",
            "created_at": now_str,
            "updated_at": now_str,
            "messages": [],
        }
        # 新会话插入到列表最前
        raw.setdefault("sessions", []).insert(0, new_sess)
        raw["active_session_id"] = sid
        self._save_raw(fingerprint, raw)
        logger.info("创建新会话: %s / %s (%s)", fingerprint, sid, new_sess["title"])
        return new_sess

    def save_session(
        self,
        fingerprint: str,
        session_id: str,
        messages: list,
        title: str | None = None,
    ) -> dict:
        """保存指定会话的消息与标题"""
        raw = self._load_raw(fingerprint)
        now_str = datetime.now().isoformat(timespec="seconds")
        target = None
        for s in raw.get("sessions", []):
            if s.get("id") == session_id:
                target = s
                break

        if target is None:
            target = {
                "id": session_id,
                "title": title or "新建对话",
                "created_at": now_str,
                "updated_at": now_str,
                "messages": messages,
            }
            raw.setdefault("sessions", []).insert(0, target)
        else:
            target["messages"] = messages
            target["updated_at"] = now_str
            if title:
                target["title"] = title

        raw["active_session_id"] = session_id
        self._save_raw(fingerprint, raw)
        logger.info("保存会话: %s / %s (%d 条消息)", fingerprint, session_id, len(messages))
        return target

    def rename_session(self, fingerprint: str, session_id: str, title: str) -> bool:
        """重命名指定会话"""
        raw = self._load_raw(fingerprint)
        for s in raw.get("sessions", []):
            if s.get("id") == session_id:
                s["title"] = title
                s["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._save_raw(fingerprint, raw)
                return True
        return False

    def set_active_session(self, fingerprint: str, session_id: str) -> bool:
        """设置当前活跃会话"""
        raw = self._load_raw(fingerprint)
        if any(s.get("id") == session_id for s in raw.get("sessions", [])):
            raw["active_session_id"] = session_id
            self._save_raw(fingerprint, raw)
            return True
        return False

    def delete_session(self, fingerprint: str, session_id: str) -> bool:
        """删除指定会话"""
        raw = self._load_raw(fingerprint)
        sessions = raw.get("sessions", [])
        initial_len = len(sessions)
        raw["sessions"] = [s for s in sessions if s.get("id") != session_id]
        if len(raw["sessions"]) == initial_len:
            return False

        if raw.get("active_session_id") == session_id:
            raw["active_session_id"] = raw["sessions"][0]["id"] if raw["sessions"] else None

        self._save_raw(fingerprint, raw)
        logger.info("删除会话: %s / %s", fingerprint, session_id)
        return True

    def sync_from_qa_traces(self, fingerprint: str) -> dict:
        """从 qa_traces 调试记录中扫描有效问答并同步/补齐到当前会话列表"""
        qa_root = self._root.parent / "qa_traces"
        if not qa_root.exists():
            return self.list_sessions(fingerprint)

        traces = []
        for p in sorted(qa_root.glob("*/*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                meta = d.get("meta", {})
                req = d.get("request", {})
                ans = d.get("answer", {})
                q = req.get("question", "").strip()
                a = ans.get("text", "").strip()
                if q and a:
                    traces.append({
                        "trace_id": meta.get("trace_id", ""),
                        "time": meta.get("created_at", ""),
                        "question": q,
                        "answer": a,
                        "thinking": ans.get("thinking", ""),
                        "sources": ans.get("source_documents", []),
                    })
            except Exception:
                pass

        if not traces:
            return self.list_sessions(fingerprint)

        raw = self._load_raw(fingerprint)
        existing_sessions = raw.get("sessions", [])
        existing_questions = set()
        for s in existing_sessions:
            for m in s.get("messages", []):
                if m.get("role") == "user" and m.get("content"):
                    existing_questions.add(str(m.get("content")).strip())

        # 过滤尚未存在于会话中的新 trace
        new_traces = [t for t in traces if t["question"] not in existing_questions]
        if new_traces:
            # 按日期分组生成新会话
            by_date = {}
            for t in new_traces:
                d_key = t["time"][:10] if len(t["time"]) >= 10 else "历史问答"
                by_date.setdefault(d_key, []).append(t)

            for d_key, t_list in by_date.items():
                first_q = t_list[0]["question"]
                title = f"{first_q[:20]}" if len(first_q) <= 20 else f"{first_q[:20]}..."
                sid = _generate_session_id()
                msgs = []
                for i, t in enumerate(t_list):
                    ts = t["time"] or datetime.now().isoformat()
                    msgs.append({"id": f"u_{sid}_{i}", "role": "user", "content": t["question"], "hasImage": False})
                    msgs.append({
                        "id": f"a_{sid}_{i}",
                        "role": "assistant",
                        "content": t["answer"],
                        "hasImage": False,
                        "sources": t["sources"],
                        "thinking": t["thinking"],
                        "trace_id": t["trace_id"],
                    })
                new_session_data = {
                    "id": sid,
                    "title": title,
                    "created_at": t_list[0]["time"] or datetime.now().isoformat(timespec="seconds"),
                    "updated_at": t_list[-1]["time"] or datetime.now().isoformat(timespec="seconds"),
                    "messages": msgs,
                }
                raw.setdefault("sessions", []).insert(0, new_session_data)

            if raw.get("sessions"):
                raw["active_session_id"] = raw["sessions"][0]["id"]
            self._save_raw(fingerprint, raw)
            logger.info("从 qa_traces 补齐了 %d 条问答会话", len(new_traces))

        return self.list_sessions(fingerprint)

    # ---------------- 兼容旧单会话接口 ----------------

    def load(self, fingerprint: str) -> dict | None:
        """读取当前活跃会话（兼容旧接口），不存在返回 None"""
        raw = self._load_raw(fingerprint)
        sessions = raw.get("sessions", [])
        if not sessions:
            return None
        active_id = raw.get("active_session_id")
        target = next((s for s in sessions if s.get("id") == active_id), sessions[0])
        return {
            "fingerprint": fingerprint,
            "updated_at": target.get("updated_at", raw.get("updated_at")),
            "messages": target.get("messages", []),
        }

    def save(self, fingerprint: str, messages: list) -> dict:
        """保存到当前活跃会话（兼容旧接口）"""
        raw = self._load_raw(fingerprint)
        active_id = raw.get("active_session_id")
        if not active_id or not any(s.get("id") == active_id for s in raw.get("sessions", [])):
            active_id = _generate_session_id()
        return self.save_session(fingerprint, active_id, messages)

    def delete(self, fingerprint: str) -> None:
        """删除全部会话记录文件"""
        fp = self._path(fingerprint)
        if fp.exists():
            fp.unlink()
            logger.info("所有聊天记录已删除: %s", fingerprint)
