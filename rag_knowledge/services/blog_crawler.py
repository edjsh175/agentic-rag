"""
多平台博客爬虫 —— 抓取文章并保存为本地 Markdown 文件

支持平台：CSDN / 博客园 / 掘金 / B站专栏 / 知乎专栏 / 微信公众号
"""
import re
import json
import time
import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import httpx
import html2text
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 合法的标题文件名：只保留中文、字母、数字、下划线、连字符
_FILENAME_SAFE = re.compile(r"[^一-鿿\w\-]", re.UNICODE)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _slug(title: str, max_len: int = 80) -> str:
    """将标题转为安全的文件名（不含 .md）"""
    safe = _FILENAME_SAFE.sub("_", title).strip("_")
    safe = re.sub(r"_+", "_", safe)
    if not safe:
        safe = "untitled"
    return safe[:max_len].rstrip("_")


def _fetch_html(url: str, **kwargs) -> str:
    """通用 HTTP GET 请求，返回 HTML"""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url, headers=_FETCH_HEADERS, **kwargs)
        resp.raise_for_status()
        return resp.text


def _fetch_json(url: str, **kwargs) -> dict:
    """通用 HTTP GET 请求，返回 JSON"""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url, headers=_FETCH_HEADERS, **kwargs)
        resp.raise_for_status()
        return resp.json()


def _post_json(url: str, payload: dict) -> dict:
    """通用 HTTP POST 请求 JSON 体，返回 JSON"""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.post(url, headers={**_FETCH_HEADERS, "Content-Type": "application/json"}, json=payload)
        resp.raise_for_status()
        return resp.json()


def _resolve_img_src(img) -> str:
    """按优先级取图片真实 URL"""
    for attr in ("data-src", "data-original", "data-cache-src", "src"):
        val = img.get(attr)
        if val and val.startswith("http"):
            return val
    return img.get("src", "")


def _html_to_markdown(content_html: str) -> str:
    """HTML → Markdown 转换（通用）"""
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_links = False
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.skip_internal_links = True
    markdown = converter.handle(content_html).strip()
    markdown = markdown.replace("\\[!\\[", "![").replace("\\](", "](").replace("\\]", "]")
    return markdown


def _download_image(url: str, image_dir: Path) -> str | None:
    """下载图片到 image_dir，返回相对路径 /scraping/<hash>.ext，失败返回 None"""
    if not url or not url.startswith("http"):
        return None
    try:
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()

        # 判断扩展名
        content_type = resp.headers.get("content-type", "")
        ext_map = {"image/jpeg": ".jpg", "image/png": ".png",
                   "image/gif": ".gif", "image/webp": ".webp",
                   "image/svg+xml": ".svg", "image/bmp": ".bmp"}
        ext = ext_map.get(content_type.split(";")[0], "")
        if not ext:
            # 从 URL 路径推断
            url_path = urlparse(url).path
            ext = Path(url_path).suffix.lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg") else ".png"

        filename = f"{url_hash}{ext}"
        dest = image_dir / filename
        if not dest.exists():
            image_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            logger.debug("图片已下载: %s → %s", url[:60], filename)
        return f"/scraping/{filename}"
    except Exception as e:
        logger.warning("图片下载失败 %s: %s", url[:50], e)
        return None


def _process_images_in_content(soup: BeautifulSoup, content_tag, image_dir: Path | None = None):
    """提取 content_tag 内所有图片，下载后替换为本地路径"""
    for img in content_tag.find_all("img"):
        src = _resolve_img_src(img)
        alt = img.get("alt", "")

        if image_dir and src:
            local_path = _download_image(src, image_dir)
            if local_path:
                src = local_path

        md_img = f"![{alt}]({src})" if src else ""
        if md_img:
            placeholder = soup.new_tag("span")
            placeholder.string = md_img
            img.replace_with(placeholder)


def _save_file(posts_dir: Path, title: str, front_matter: dict, markdown: str) -> str:
    """保存 Markdown 文件到 posts_dir，返回 file_path"""
    posts_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in front_matter.items():
        if v is not None:
            lines.append(f"{k}: {v}")
    lines.append("---\n")
    lines.append(markdown)
    full_content = "\n".join(lines)

    stem = _slug(title)
    fp = posts_dir / f"{stem}.md"
    counter = 1
    while fp.exists():
        fp = posts_dir / f"{stem}_{counter}.md"
        counter += 1
    fp.write_text(full_content, encoding="utf-8")
    logger.info("已保存: %s", fp)
    return str(fp)


# ------------------------------------------------------------------
# 基类
# ------------------------------------------------------------------

class BaseCrawler(ABC):
    """爬虫基类"""

    def __init__(self, posts_dir: Path, image_dir: Path | None = None):
        self.posts_dir = posts_dir
        self.image_dir = image_dir

    @abstractmethod
    def crawl(self, url: str) -> dict:
        """抓取文章，返回 { title, source_url, author, platform, publish_date, markdown, file_path }"""
        ...


# ------------------------------------------------------------------
# CSDN
# ------------------------------------------------------------------

class CSDNCrawler(BaseCrawler):
    """CSDN 博客文章爬虫"""

    def crawl(self, url: str) -> dict:
        if not url.startswith("https://blog.csdn.net/"):
            raise ValueError("仅支持 CSDN 博客链接（https://blog.csdn.net/...）")

        logger.info("开始抓取 CSDN: %s", url)
        html = _fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # 标题
        title_tag = soup.select_one("h1.title-article") or soup.select_one("h1.article-title") or soup.select_one("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"

        # 作者
        author_tag = soup.select_one("a.follow-nickName") or soup.select_one(".blog-user-name")
        author = author_tag.get_text(strip=True) if author_tag else "unknown"

        # 发布日期
        date_tag = soup.select_one("span.time")
        publish_date = date_tag.get_text(strip=True).replace("\n", "").strip() if date_tag else None

        # 正文
        content_tag = soup.select_one("div#article_content") or soup.select_one("article.article-content")
        if not content_tag:
            raise ValueError("未能找到文章正文，页面结构可能已变更")

        for tag in content_tag.select("script, style, .article-copyright, .hide-article-box, .blog-tags-box"):
            tag.decompose()

        _process_images_in_content(soup, content_tag, self.image_dir)
        markdown = _html_to_markdown(str(content_tag))

        front_matter = {
            "title": title, "author": author, "source": url,
            "platform": "CSDN", "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if publish_date:
            front_matter["publish_date"] = publish_date

        file_path = _save_file(self.posts_dir, title, front_matter, markdown)
        return {"title": title, "source_url": url, "author": author, "platform": "CSDN",
                "publish_date": publish_date, "markdown": markdown, "file_path": file_path}


# ------------------------------------------------------------------
# 博客园
# ------------------------------------------------------------------

class CnblogCrawler(BaseCrawler):
    """博客园文章爬虫"""

    def crawl(self, url: str) -> dict:
        if not re.match(r"https?://(www\.)?cnblogs\.com/", url):
            raise ValueError("仅支持博客园链接（https://www.cnblogs.com/...）")

        logger.info("开始抓取 博客园: %s", url)
        html = _fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # 标题
        title_tag = soup.select_one("#cb_post_title_url") or soup.select_one(".postTitle") or soup.select_one("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"

        # 作者
        author_tag = soup.select_one(".postDesc") or soup.select_one(".header")
        author = "unknown"
        if author_tag:
            t = author_tag.get_text(strip=True)
            m = re.search(r'[（(]?(\w+)[）)]', t)
            if m:
                author = m.group(1)
            elif "@" not in t[:20]:
                author = t.split()[0] if t.split() else author

        # 发布日期
        date_tag = soup.select_one("#post-date") or soup.select_one(".entry-meta")
        publish_date = date_tag.get_text(strip=True) if date_tag else None

        # 正文
        content_tag = soup.select_one("#cnblogs_post_body") or soup.select_one("#post_detail")
        if not content_tag:
            raise ValueError("未能找到文章正文")

        for tag in content_tag.select("script, style"):
            tag.decompose()

        _process_images_in_content(soup, content_tag, self.image_dir)
        markdown = _html_to_markdown(str(content_tag))

        front_matter = {
            "title": title, "author": author, "source": url,
            "platform": "博客园", "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if publish_date:
            front_matter["publish_date"] = publish_date

        file_path = _save_file(self.posts_dir, title, front_matter, markdown)
        return {"title": title, "source_url": url, "author": author, "platform": "博客园",
                "publish_date": publish_date, "markdown": markdown, "file_path": file_path}


# ------------------------------------------------------------------
# 微信公众号
# ------------------------------------------------------------------

class WechatCrawler(BaseCrawler):
    """微信公众号文章爬虫"""

    def crawl(self, url: str) -> dict:
        if not re.match(r"https?://mp\.weixin\.qq\.com/", url):
            raise ValueError("仅支持微信公众号链接（https://mp.weixin.qq.com/...）")

        logger.info("开始抓取 微信公众号: %s", url)
        html = _fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # 标题
        title_tag = soup.select_one("#activity-name") or soup.select_one("h2") or soup.select_one("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"

        # 作者
        author_tag = soup.select_one("#js_name") or soup.select_one(".rich_media_meta_text")
        author = author_tag.get_text(strip=True) if author_tag else "unknown"

        # 发布日期
        publish_date = None
        date_tag = soup.select_one("#publish_time")
        if date_tag:
            publish_date = date_tag.get_text(strip=True)
        else:
            m = re.search(r'var create_time\s*=\s*"?(\d{4}-\d{2}-\d{2})', html)
            if m:
                publish_date = m.group(1)

        # 正文
        content_tag = soup.select_one("#rich_media_content") or soup.select_one(".rich_media_content")
        if not content_tag:
            raise ValueError("未能找到文章正文")

        for tag in content_tag.select("script, style"):
            tag.decompose()

        _process_images_in_content(soup, content_tag, self.image_dir)
        markdown = _html_to_markdown(str(content_tag))

        front_matter = {
            "title": title, "author": author, "source": url,
            "platform": "微信公众号", "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if publish_date:
            front_matter["publish_date"] = publish_date

        file_path = _save_file(self.posts_dir, title, front_matter, markdown)
        return {"title": title, "source_url": url, "author": author, "platform": "微信公众号",
                "publish_date": publish_date, "markdown": markdown, "file_path": file_path}


# ------------------------------------------------------------------
# 掘金（API 调用）
# ------------------------------------------------------------------

class JuejinCrawler(BaseCrawler):
    """掘金文章爬虫（通过 API）"""

    def crawl(self, url: str) -> dict:
        if not re.match(r"https?://juejin\.cn/post/", url):
            raise ValueError("仅支持掘金链接（https://juejin.cn/post/...）")

        logger.info("开始抓取 掘金: %s", url)
        article_id = url.rstrip("/").rsplit("/", 1)[-1]

        # 通过掘金 API 获取文章内容（client_type=1 是必需的）
        payload = {"article_id": article_id, "client_type": 1}
        raw = _post_json("https://api.juejin.cn/content_api/v1/article/detail", payload)
        if not isinstance(raw, dict):
            raise ValueError("掘金 API 返回数据格式异常")

        d = raw.get("data")
        if not isinstance(d, dict):
            # 尝试备用接口
            logger.info("掘金主 API 无数据，尝试备用接口")
            raw = _post_json("https://api.juejin.cn/interact_api/v1/article/get", payload)
            d = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(d, dict):
            raise ValueError(f"掘金 API 返回数据异常，文章可能不存在或无法访问 (ID: {article_id})")

        article_info = d.get("article_info") or d
        author_info = d.get("author_user_info") or {}

        title = article_info.get("title", "无标题")
        author = author_info.get("user_name") or article_info.get("author_name", "unknown")

        publish_date = article_info.get("publish_time", "")
        if publish_date and len(str(publish_date)) == 10:
            publish_date = datetime.fromtimestamp(int(publish_date)).strftime("%Y-%m-%d")
        else:
            publish_date = None

        # 掘金 API 返回 content 为 HTML 格式
        content_html = article_info.get("mark_content") or article_info.get("content", "")
        if not content_html:
            raise ValueError("掘金 API 返回内容为空")

        # 检查是否为纯文本/Markdown（不含 HTML 标签）
        if "<" not in content_html[:200]:
            markdown = content_html
        else:
            soup = BeautifulSoup(content_html, "html.parser")
            for tag in soup.select("script, style"):
                tag.decompose()
            _process_images_in_content(soup, soup, self.image_dir)
            markdown = _html_to_markdown(str(soup))

        if not markdown.strip():
            raise ValueError("转换后的文章内容为空")

        front_matter = {
            "title": title, "author": author, "source": url,
            "platform": "掘金", "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if publish_date:
            front_matter["publish_date"] = publish_date

        file_path = _save_file(self.posts_dir, title, front_matter, markdown)
        return {"title": title, "source_url": url, "author": author, "platform": "掘金",
                "publish_date": publish_date, "markdown": markdown, "file_path": file_path}


# ------------------------------------------------------------------
# 自动路由
# ------------------------------------------------------------------

_PLATFORM_ROUTING: list[tuple[re.Pattern, str, type[BaseCrawler]]] = [
    (re.compile(r"blog\.csdn\.net"), "CSDN", CSDNCrawler),
    (re.compile(r"cnblogs\.com"), "博客园", CnblogCrawler),
    (re.compile(r"juejin\.cn"), "掘金", JuejinCrawler),
    (re.compile(r"mp\.weixin\.qq\.com"), "微信公众号", WechatCrawler),
]


def detect_platform(url: str) -> str | None:
    """根据 URL 检测所属平台"""
    for pattern, platform_name, _ in _PLATFORM_ROUTING:
        if pattern.search(url):
            return platform_name
    return None


def create_crawler(url: str, posts_dir: Path, image_dir: Path | None = None) -> BaseCrawler:
    """根据 URL 创建对应的爬虫实例"""
    for pattern, _, crawler_cls in _PLATFORM_ROUTING:
        if pattern.search(url):
            return crawler_cls(posts_dir, image_dir)
    raise ValueError(f"不支持的平台，URL: {url}")
