"""
联网搜索服务 —— 基于 DuckDuckGo（免费，无需 API Key）
"""
import warnings
import logging
from ddgs import DDGS

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)



class WebSearch:
    """联网搜索"""

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """搜索并返回 [{title, snippet, url}]"""
        try:
            with DDGS() as ddgs:
                results = []
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("href", ""),
                    })
                logger.info("联网搜索: \"%s\" → %d 条结果", query, len(results))
                return results
        except Exception as e:
            logger.warning("联网搜索失败: %s", e)
            return []
