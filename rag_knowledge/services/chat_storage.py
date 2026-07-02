"""
聊天记录服务 —— 以 JSON 文件形式将对话持久化到服务器

每个浏览器指纹对应一个 data/chats/{fingerprint}.json 文件
"""
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class ChatStorage:
    """聊天记录读写"""

    def __init__(self, data_dir: Path):
        self._root = data_dir / "chats"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, fingerprint: str) -> Path:
        return self._root / f"{fingerprint}.json"

    def load(self, fingerprint: str) -> dict | None:
        """读取聊天记录，不存在返回 None"""
        fp = self._path(fingerprint)
        if not fp.exists():
            return None
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("读取聊天记录失败 %s: %s", fingerprint, e)
            return None

    def save(self, fingerprint: str, messages: list) -> dict:
        """全量覆写聊天记录"""
        data = {
            "fingerprint": fingerprint,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "messages": messages,
        }
        fp = self._path(fingerprint)
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("聊天记录已保存: %s (%d 条)", fingerprint, len(messages))
        return data

    def delete(self, fingerprint: str) -> None:
        """删除聊天记录文件"""
        fp = self._path(fingerprint)
        if fp.exists():
            fp.unlink()
            logger.info("聊天记录已删除: %s", fingerprint)
