"""
测试数据集构建与管理

数据集格式 (JSON):
[
  {
    "question": "如何使用 Ollama 部署模型？",
    "relevant_chunk_ids": ["uuid-1"],
    "kb_name": "文章附件",
    "difficulty": "easy"
  }
]

构建方式：
  1. 从 ChromaDB 拉取所有 approved 的 chunk
  2. 用 LLM 为每个 chunk 自动生成 2-3 个相关问题
  3. chunk_id 即为该问题的「相关文档」金标准
"""
import json
import time
import logging
import random
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

import httpx

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore

logger = logging.getLogger(__name__)

_COLLOQUIAL_REWRITES = [
    ("如何", "怎么"),
    ("为什么", "为啥"),
    ("需要", "得"),
    ("是否", "是不是"),
    ("哪个", "哪个"),
    ("无法", "看不到"),
    ("执行", "跑"),
    ("配置", "设置"),
]
_ABBREVIATIONS = [
    ("StampNodeServer", "StampNode"),
    ("StampServer", "Stamp"),
    ("StampTools", "工具"),
    ("TongWeb", "东通"),
    ("东方通", "东通"),
    ("RockyLinux9", "Rocky9"),
]
_TYPO_REWRITES = [
    ("服务", "服物"),
    ("配置", "配值"),
    ("权限", "权现"),
    ("驱动", "驱东"),
    ("部署", "部属"),
]


def _extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """用 jieba 提取高频关键词。"""
    import jieba
    words = [w for w in jieba.cut(text) if len(w) >= 2]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    sorted_words = sorted(freq, key=freq.get, reverse=True)
    return sorted_words[:top_n]


# ----------------------------------------------------------------
# 用 LLM 从 chunk 内容生成问题
# ----------------------------------------------------------------

_QUESTION_GEN_PROMPT = """你是一个测试用例生成助手。阅读以下文档片段，生成 {n} 个可以用该片段回答的问题。

规则：
1. 问题应自然、多样，模拟真实用户提问方式
2. 问题应覆盖片段中的关键信息
3. 包含简单的事实型问题和需要推理的问题
4. 问题使用中文
5. 输出纯 JSON 数组，不要包含任何其他文字

文档片段：
{chunk_text}

输出示例：["问题1", "问题2", "问题3"]
JSON 数组："""


def _generate_questions(
    chunk_text: str,
    n: int = 3,
    ollama_base: str = None,
    llm_model: str = None,
    timeout: int = 60,
) -> List[str]:
    """用 LLM 从单个 chunk 生成 n 个问题"""
    if ollama_base is None:
        ollama_base = Config().ollama_base_url
    if llm_model is None:
        llm_model = Config().llm_model

    prompt = _QUESTION_GEN_PROMPT.format(n=n, chunk_text=chunk_text[:1200])

    try:
        resp = httpx.post(
            f"{ollama_base}/api/chat",
            json={
                "model": llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 512, "top_k": 40},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "").strip()

        # 提取 JSON 数组（Ollama 可能在前后加文字）
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1 and end > start:
            questions = json.loads(content[start : end + 1])
            if isinstance(questions, list):
                return [q for q in questions if isinstance(q, str) and len(q) > 3]
    except Exception as e:
        logger.warning("问题生成失败: %s", e)

    return []


def _rewrite_colloquial(question: str) -> str:
    rewritten = question
    for src, dst in _COLLOQUIAL_REWRITES:
        rewritten = rewritten.replace(src, dst)
    if not rewritten.endswith(("吗？", "呢？", "？")):
        rewritten = f"{rewritten}呢？"
    return rewritten


def _rewrite_typo_abbr(question: str) -> str:
    rewritten = question
    for src, dst in _ABBREVIATIONS:
        rewritten = rewritten.replace(src, dst)
    for src, dst in _TYPO_REWRITES:
        if src in rewritten:
            rewritten = rewritten.replace(src, dst, 1)
            break
    rewritten = rewritten.replace("？", "")
    return f"{rewritten}咋整？"


def _rewrite_version_ambiguous(question: str, source: str = "") -> str:
    if re.search(r"(版本|Rocky|Windows|Linux|TongWeb|Tomcat|MySQL|\bv\d)", question, re.I):
        return question.replace("？", "，不同版本是不是都一样？")
    if source and "Rocky9" in source:
        return f"{question.rstrip('？')}，如果不是 Rocky9 也一样吗？"
    return f"{question.rstrip('？')}，这个在不同版本上是不是一样？"


def build_hardcase_dataset(dataset: List[dict]) -> List[dict]:
    """
    基于现有标注问题扩展难例集。

    每个知识点至少生成：
      - standard: 原始问法
      - colloquial: 口语化
      - typo_abbr: 错别字 / 简称
      - version_ambiguous: 版本歧义
    """
    hardcases: List[dict] = []
    for item in dataset:
        base = dict(item)
        base_question = item["question"]
        source = item.get("source", "")
        variants = {
            "standard": base_question,
            "colloquial": _rewrite_colloquial(base_question),
            "typo_abbr": _rewrite_typo_abbr(base_question),
            "version_ambiguous": _rewrite_version_ambiguous(base_question, source),
        }
        for question_type, question in variants.items():
            hardcases.append({
                **base,
                "base_question": base_question,
                "question": question,
                "question_type": question_type,
            })
    return hardcases


# ----------------------------------------------------------------
# 数据集构建器
# ----------------------------------------------------------------


class TestDatasetBuilder:
    """从知识库中自动构建测试数据集"""
    __test__ = False

    def __init__(self, questions_per_chunk: int = 3, max_chunks: int = 200):
        """
        questions_per_chunk: 每个 chunk 生成的问题数
        max_chunks: 最多采样多少个 chunk（避免 API 调用过多）
        """
        self._questions_per_chunk = questions_per_chunk
        self._max_chunks = max_chunks

    def build(self, output_path: str | Path) -> List[dict]:
        """
        从知识库构建测试数据集，保存为 JSON 文件

        返回: 数据集列表
        """
        cfg = Config()
        store = VectorStore()
        chroma = store.get_chroma()

        # 拉取所有 approved 的文档
        all_data = chroma.get(where={"review_status": "approved"})
        if not all_data or not all_data.get("ids"):
            # 回退：没有 approved 的 chunk 时，使用全部 chunk
            # 这通常意味着 review_status 字段尚未被设置（旧数据迁移前）
            logger.warning(
                "知识库中无 approved 状态的 chunk（共 %d 个 chunk 缺少 review_status），"
                "回退为使用全部 chunk 构建测试集。"
                "请将需要测试的 chunk 的 review_status 设为 'approved'，或运行数据迁移。",
                VectorStore().count(),
            )
            all_data = chroma.get()

        total = len(all_data["ids"])
        logger.info("知识库共有 %d 个 approved chunks", total)

        # 采样（避免调用 LLM 过多）
        sample_size = min(self._max_chunks, total)
        indices = random.sample(range(total), sample_size)

        dataset = []
        for idx in indices:
            chunk_id = all_data["ids"][idx]
            chunk_text = all_data["documents"][idx]
            metadata = all_data["metadatas"][idx] if all_data.get("metadatas") else {}

            if not chunk_text or len(chunk_text) < 50:
                continue  # 太短的跳过

            questions = _generate_questions(
                chunk_text,
                n=self._questions_per_chunk,
                ollama_base=cfg.ollama_base_url,
                llm_model=cfg.llm_model,
            )

            content_fp = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:16]
            section_path = metadata.get("section_path", "") or metadata.get("section_title", "")
            source_val = metadata.get("source", "")
            chunk_keywords = _extract_keywords(chunk_text, top_n=5)

            for q in questions:
                dataset.append({
                    "question": q,
                    "relevant_chunk_ids": [chunk_id],
                    "chunk_ids": [chunk_id],
                    "kb_name": metadata.get("kb_name", ""),
                    "doc_category": metadata.get("doc_category", ""),
                    "source": source_val,
                    "expected_targets": [{
                        "source": source_val,
                        "section_path": section_path,
                        "keywords": chunk_keywords,
                        "content_fingerprint": content_fp,
                    }],
                })

            logger.info(
                "已处理 %d/%d: chunk=%s → %d 个问题",
                len([d for d in dataset if d["relevant_chunk_ids"][0] == chunk_id]),
                sample_size,
                chunk_id[:8],
                len(questions),
            )

            # 请求之间稍作延迟，避免压垮 Ollama
            time.sleep(0.3)

        # 保存
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)

        logger.info("测试数据集已保存到 %s，共 %d 条", output_path, len(dataset))
        return dataset


def load_dataset(path: str | Path) -> List[dict]:
    """加载已有的测试数据集"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class HardCaseDatasetBuilder:
    """基于已有评测集扩展检索难例。"""

    def build_from_dataset(self, dataset_path: str | Path, output_path: str | Path) -> List[dict]:
        dataset = load_dataset(dataset_path)
        hardcases = build_hardcase_dataset(dataset)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(hardcases, f, ensure_ascii=False, indent=2)

        logger.info("难例评测集已保存到 %s，共 %d 条", output_path, len(hardcases))
        return hardcases


def get_dataset_stats(dataset: List[dict]) -> dict:
    """获取数据集统计信息"""
    if not dataset:
        return {"total": 0}
    kbs = {}
    for item in dataset:
        kb = item.get("kb_name", "unknown")
        kbs[kb] = kbs.get(kb, 0) + 1
    return {
        "total": len(dataset),
        "by_kb": kbs,
        "avg_chunks_per_question": sum(len(d.get("relevant_chunk_ids", [])) for d in dataset) / len(dataset),
    }
