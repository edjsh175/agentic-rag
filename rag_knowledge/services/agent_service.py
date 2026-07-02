"""
智能体预设服务 —— 从 data/agents.json 加载预设智能体
"""
import json
import logging
from pathlib import Path

from rag_knowledge.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_AGENTS = [
    {
        "id": "general",
        "name": "通用助手",
        "icon": "\U0001f916",
        "description": "默认助手，结合知识库和专业能力回答各类问题",
        "system_prompt": "你是通用型项目知识库助手。优先给出简洁、明确的中文回答，保留必要的专业术语，并根据内容使用 Markdown、代码块或表格。",
    }
]


def load_agents() -> list[dict]:
    """加载智能体列表，文件不存在时返回默认通用助手"""
    path = Config().data_dir / "agents.json"
    if not path.exists():
        return _DEFAULT_AGENTS
    try:
        with open(path, encoding="utf-8") as f:
            agents = json.load(f)
        if not agents:
            return _DEFAULT_AGENTS
        return agents
    except Exception as e:
        logger.warning("智能体文件读取失败: %s", e)
        return _DEFAULT_AGENTS
