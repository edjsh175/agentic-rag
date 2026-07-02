"""
文件加载服务 —— 支持多格式文件的文本提取与分块

支持的格式：
  - 文本类：PDF / DOCX / TXT        → 提取文字 + 内嵌图片由视觉模型描述
  - 图片类：jpg/png/gif/bmp/webp    → qwen3-vl 视觉模型生成描述
  - 视频类：mp4/avi/mov/mkv         → 提取关键帧 → 视觉模型描述 → 合并
"""
import os
import time
import base64
import tempfile
import zipfile
import logging
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.models.document import FileCategory
from rag_knowledge.services.unstructured_loader import UnstructuredChapterLoader, SUPPORTED_EXTS

logger = logging.getLogger(__name__)

# 各格式扩展名集合
TEXT_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv"}
WORD_FIELD_RE = re.compile(r"(?im)^\s*(HYPERLINK|PAGEREF|TOC|MERGEFIELD|SEQ|REF)\b.*$")
TOC_LINE_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*)?\s*[\u4e00-\u9fffA-Za-z].*?(?:\.{2,}|…{2,}|\s{2,})\s*\d+\s*$")
URL_LINE_RE = re.compile(r"^\s*(?:https?://\S+|www\.\S+|\[[^\]]+\]\(https?://[^)]+\))\s*$", re.I)
VERSION_HINT_RE = re.compile(r"(?:v?\d+(?:\.\d+)+|rocky\s*\d+|centos\s*\d+|windows\s*server\s*\d+)", re.I)


class FileLoader:
    """多格式文件加载器"""

    def __init__(self):
        cfg = Config()
        self._chunk_size = cfg.chunk_size
        self._chunk_overlap = cfg.chunk_overlap
        self._ollama_base = cfg.ollama_base_url
        self._vision_model = cfg.vision_model
        self._extract_images = cfg.extract_embedded_images
        # 按自然语言段落边界切分，避免切断语义
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        )
        self._http: httpx.Client | None = None

        # unstructured 章节切片（可配置开关）
        self._use_unstructured = cfg.use_unstructured
        self._unstructured_loader: UnstructuredChapterLoader | None = None
        if self._use_unstructured:
            self._unstructured_loader = UnstructuredChapterLoader(
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
                strategy=cfg.unstructured_strategy,
            )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def load(self, file_path: str) -> tuple[list[Document], str]:
        """
        加载并处理一个文件

        参数：
          file_path: 文件绝对路径

        返回：
          (chunks, category)
          chunks   — 切分后的 Document 列表
          category — 文件分类（text / image / video）

        抛出：
          ValueError — 不支持的文件类型
        """
        suffix = Path(file_path).suffix.lower()

        if suffix in TEXT_EXTS:
            return self._load_text(file_path), FileCategory.TEXT
        elif suffix in IMAGE_EXTS:
            return self._load_image(file_path), FileCategory.IMAGE
        elif suffix in VIDEO_EXTS:
            return self._load_video(file_path), FileCategory.VIDEO
        else:
            raise ValueError(f"不支持的文件类型: {suffix}")

    @staticmethod
    def detect_category(file_path: str) -> str | None:
        """根据后缀判断文件分类"""
        suffix = Path(file_path).suffix.lower()
        if suffix in TEXT_EXTS:
            return FileCategory.TEXT
        if suffix in IMAGE_EXTS:
            return FileCategory.IMAGE
        if suffix in VIDEO_EXTS:
            return FileCategory.VIDEO
        return None

    # ------------------------------------------------------------------
    # 文本类
    # ------------------------------------------------------------------

    def _load_text(self, file_path: str) -> list[Document]:
        """文本类文件加载，优先 unstructured 章节切片，失败回退旧逻辑"""
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".doc":
            logger.warning("检测到旧版 .doc 文件，建议先转换为 .docx 再入库: %s", path.name)

        # 优先使用 unstructured 按标题切片（仅 .txt / .md / .docx）
        if self._use_unstructured and suffix in SUPPORTED_EXTS:
            try:
                chunks = self._unstructured_loader.load(file_path)
            except Exception as e:
                logger.warning("unstructured 解析失败，回退旧解析方式 %s: %s", path.name, e)
                chunks = self._load_text_legacy(file_path)
        else:
            chunks = self._load_text_legacy(file_path)

        # 提取内嵌图片并用视觉模型描述，每条描述作为独立 chunk
        if suffix in (".pdf", ".docx") and self._extract_images:
            image_descs = self._describe_embedded_images(file_path)
            for desc in image_descs:
                chunks.append(Document(
                    page_content=desc,
                    metadata={
                        "source": path.name,
                        "category": FileCategory.TEXT,
                        "content_type": "embedded_image",
                        "section_title": "",
                        "section_path": "",
                    },
                ))

        return self._post_process_chunks(chunks)

    def _load_text_legacy(self, file_path: str) -> list[Document]:
        """旧版文本加载逻辑（固定长度切分）"""
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            loader = PyPDFLoader(str(path))
            docs = loader.load()
        elif suffix == ".docx":
            loader = Docx2txtLoader(str(path))
            docs = loader.load()
        elif suffix == ".doc":
            docs = self._load_old_doc(file_path)
        else:
            loader = TextLoader(str(path), encoding="utf-8")
            docs = loader.load()

        for d in docs:
            d.metadata["source"] = path.name
            d.metadata["category"] = FileCategory.TEXT
            d.page_content = self._sanitize_text(d.page_content)

        return self._splitter.split_documents(docs)

    def _load_old_doc(self, file_path: str) -> list[Document]:
        """提取旧版 Word .doc 文件（OLE2 二进制格式）中的文本

        支持 Unicode (UTF-16LE) 和 ANSI (GBK/系统代码页) 编码的文档。
        """
        path = Path(file_path)
        try:
            import olefile
        except ImportError:
            logger.warning("缺少 olefile 库，无法解析 .doc 文件: %s", path.name)
            return [Document(
                page_content=f"[需要安装 olefile 以支持旧版 .doc 文件] {path.name}",
                metadata={"source": path.name},
            )]

        try:
            ole = olefile.OleFileIO(file_path)
        except Exception as e:
            logger.error("无法打开 OLE2 容器 %s: %s", path.name, e)
            raise ValueError(f"无法解析旧版 .doc 文件: {path.name}")

        try:
            if not ole.exists("WordDocument"):
                raise ValueError(f"文件中未找到 WordDocument 流: {path.name}")

            stream = ole.openstream("WordDocument")
            data = stream.read()
            stream.close()

            import struct

            # 从 FIB 读取关键字段
            flags = struct.unpack_from("<H", data, 0x0A)[0] if len(data) > 12 else 0
            fc_min = struct.unpack_from("<I", data, 0x18)[0] if len(data) > 28 else 0
            ccp_text = struct.unpack_from("<I", data, 0x4C)[0] if len(data) > 80 else 0

            is_complex = bool(flags & 0x0200)  # fComplex 标志

            text = ""

            if not is_complex and ccp_text > 0:
                # ---- 非复杂格式：文本是连续块 ----
                start = max(fc_min, 0)
                try:
                    raw = data[start:start + ccp_text]
                    text = raw.decode("gbk").strip("\r\n\t\x00")
                except UnicodeDecodeError:
                    pass

                if not text:
                    try:
                        raw = data[start:start + ccp_text * 2]
                        text = raw.decode("utf-16-le", errors="ignore").strip("\r\n\t\x00")
                    except Exception:
                        pass

            if not text:
                candidates = []
                for start in sorted({fc_min, 0x200, 0x00}):
                    if start > 0 and start < len(data) - 100:
                        for enc, stride in [("gbk", 1), ("utf-16-le", 2), ("utf-8", 1)]:
                            raw_len = ccp_text * stride if ccp_text > 0 else min(len(data) - start, 50000)
                            if start + raw_len > len(data):
                                raw_len = len(data) - start
                            try:
                                decoded = data[start:start + raw_len].decode(enc, errors="replace")
                                good = sum(1 for c in decoded if c.isprintable() or c in "\n\r\t")
                                score = good / max(len(decoded), 1)
                                if score > 0.3:
                                    candidates.append((score, decoded))
                            except Exception:
                                continue

                if candidates:
                    candidates.sort(key=lambda x: -x[0])
                    text = candidates[0][1]

            if text:
                text = text.replace("�", "")
                text = "".join(c for c in text if c.isprintable() or c in "\n\r\t")
                text = "\n".join(line.strip() for line in text.split("\n") if line.strip())

            if not text:
                logger.warning("未能从 .doc 文件中提取到文本: %s", path.name)
                text = f"[未能提取文本的 Word 文档] {path.name}"

        except Exception as e:
            logger.error("解析 .doc 文件失败 %s: %s", path.name, e)
            raise
        finally:
            ole.close()

        doc = Document(
            page_content=text,
            metadata={"source": path.name},
        )
        return [doc]

    def _post_process_chunks(self, chunks: list[Document]) -> list[Document]:
        """统一清洗分块内容，过滤低信息噪声块。"""
        cleaned_chunks: list[Document] = []
        for chunk in chunks:
            cleaned = self._sanitize_text(chunk.page_content)
            if not cleaned or self._is_low_information(cleaned):
                continue
            chunk.page_content = cleaned
            cleaned_chunks.append(chunk)
        return cleaned_chunks

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """清洗 Word 域代码、无效链接和多余空白。"""
        if not text:
            return ""

        cleaned = text.replace("\x13", " ").replace("\x14", " ").replace("\x15", " ")
        cleaned = WORD_FIELD_RE.sub("", cleaned)
        cleaned = re.sub(r"(?im)^\s*TOC\s+\\[^\n]*$", "", cleaned)
        cleaned = re.sub(r"(?im)^\s*PAGEREF\s+[^\n]*$", "", cleaned)
        cleaned = re.sub(r"(?im)^\s*HYPERLINK\s+\"[^\n]*$", "", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _is_low_information(text: str) -> bool:
        """过滤目录块、纯链接块和极短噪声块。"""
        stripped = text.strip()
        if not stripped:
            return True

        compact = re.sub(r"[\W_]+", "", stripped, flags=re.UNICODE)
        if len(compact) < 8:
            return True

        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            return True

        toc_hits = sum(1 for line in lines if TOC_LINE_RE.match(line))
        if len(lines) >= 2 and toc_hits / len(lines) >= 0.6:
            return True

        url_hits = sum(1 for line in lines if URL_LINE_RE.match(line))
        if url_hits == len(lines):
            return True

        if len(lines) == 1 and len(compact) < 16 and not VERSION_HINT_RE.search(stripped):
            return True

        return False

    # ------------------------------------------------------------------
    # 内嵌图片提取（Word / PDF 中的图片 → 视觉模型描述）
    # ------------------------------------------------------------------

    def _describe_embedded_images(self, file_path: str, max_images: int = 5) -> list[str]:
        """提取文档内嵌图片并调用视觉模型描述，返回描述文本列表"""
        suffix = Path(file_path).suffix.lower()
        if suffix == ".docx":
            return self._describe_docx_images(file_path, max_images)
        elif suffix == ".pdf":
            return self._describe_pdf_images(file_path, max_images)
        return []

    def _describe_docx_images(self, file_path: str, max_images: int) -> list[str]:
        """从 DOCX（zip 包）中提取内嵌图片 → 视觉模型描述"""
        path = Path(file_path)
        descs: list[str] = []
        try:
            with zipfile.ZipFile(file_path) as z:
                rels_path = "word/_rels/document.xml.rels"
                if rels_path not in z.namelist():
                    return []

                # 解析关系文件，映射 rId → 图片路径
                rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
                rels_xml = ET.parse(z.open(rels_path))
                rid_map: dict[str, str] = {}
                for rel in rels_xml.findall(f"{{{rels_ns}}}Relationship"):
                    rid = rel.get("Id", "")
                    target = rel.get("Target", "")
                    img_exts = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
                    if any(target.lower().endswith(ext) for ext in img_exts):
                        full_path = f"word/{target}"
                        if full_path in z.namelist():
                            rid_map[rid] = full_path

                if not rid_map:
                    return []

                rids = list(rid_map.keys())[:max_images]
                for i, rid in enumerate(rids):
                    img_path = rid_map[rid]
                    img_data = z.open(img_path).read()
                    ext = Path(img_path).suffix
                    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
                    tmp.write(img_data)
                    tmp.close()
                    try:
                        desc = self._call_vision(tmp.name)
                        if desc:
                            descs.append(f"[{path.name} 图片{i + 1}]\n{desc.strip()}")
                    finally:
                        if os.path.exists(tmp.name):
                            os.unlink(tmp.name)
        except Exception as e:
            logger.warning("提取 DOCX 内嵌图片失败 %s: %s", path.name, e)
        return descs

    def _describe_pdf_images(self, file_path: str, max_images: int) -> list[str]:
        """从 PDF 中提取内嵌图片 → 视觉模型描述（需要 PyMuPDF）"""
        path = Path(file_path)
        descs: list[str] = []
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("缺少 PyMuPDF，无法提取 PDF 内嵌图片: %s", path.name)
            return []

        try:
            doc = fitz.open(file_path)
            count = 0
            for page_num in range(len(doc)):
                if count >= max_images:
                    break
                images = doc[page_num].get_images(full=True)
                for img_info in images:
                    if count >= max_images:
                        break
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    img_bytes = base_image["image"]
                    ext = base_image["ext"]

                    tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
                    tmp.write(img_bytes)
                    tmp.close()
                    try:
                        desc = self._call_vision(tmp.name)
                        if desc:
                            count += 1
                            descs.append(f"[{path.name} 图片{count}]\n{desc.strip()}")
                    finally:
                        if os.path.exists(tmp.name):
                            os.unlink(tmp.name)
            doc.close()
        except Exception as e:
            logger.warning("提取 PDF 内嵌图片失败 %s: %s", path.name, e)
        return descs

    # ------------------------------------------------------------------
    # 图片类
    # ------------------------------------------------------------------

    def _load_image(self, file_path: str) -> list[Document]:
        """图片 → 压缩 → 视觉模型描述 → 分块"""
        path = Path(file_path)
        t0 = time.time()
        logger.info("处理图片: %s", path.name)

        try:
            processed = self._resize_image(file_path, max_px=1024)
        except Exception as e:
            logger.error("图片压缩失败 %s: %s", path.name, e)
            processed = file_path

        try:
            desc = self._call_vision(processed)
        finally:
            if processed != file_path and os.path.exists(processed):
                os.unlink(processed)

        content = desc.strip() or f"[未能生成描述的图片] {path.name}"
        doc = Document(
            page_content=content,
            metadata={"source": path.name, "category": FileCategory.IMAGE, "file_path": str(path)},
        )
        result = self._splitter.split_documents([doc])
        elapsed = time.time() - t0
        logger.info("图片处理完成: %s | %d 块 | %.2fs", path.name, len(result), elapsed)
        return result

    # ------------------------------------------------------------------
    # 视频类
    # ------------------------------------------------------------------

    def _load_video(self, file_path: str) -> list[Document]:
        """视频 → 提取 N 个关键帧 → 逐一描述 → 合并分块"""
        path = Path(file_path)
        t0 = time.time()
        logger.info("处理视频: %s", path.name)

        descs = self._extract_frames(file_path)
        if not descs:
            content = f"[视频文件] {path.name}（无法提取帧）"
        else:
            parts = "\n\n".join(
                f"--- 第 {i + 1} 帧 ---\n{t}" for i, t in enumerate(descs)
            )
            content = f"视频 [{path.name}] 内容描述：\n{parts}"

        doc = Document(
            page_content=content,
            metadata={"source": path.name, "category": FileCategory.VIDEO, "file_path": str(path)},
        )
        result = self._splitter.split_documents([doc])
        elapsed = time.time() - t0
        logger.info("视频处理完成: %s | %d 块 | %.2fs", path.name, len(result), elapsed)
        return result

    def _extract_frames(self, video_path: str, n: int = 3) -> list[str]:
        """用 OpenCV 提取均匀分布的 n 帧并逐帧描述"""
        try:
            import cv2
        except ImportError:
            logger.warning("缺少 opencv-python-headless，跳过视频: %s", video_path)
            return ["[需要安装 opencv-python-headless 以支持视频处理]"]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if total <= 0:
            cap.release()
            return []

        descs = []
        for i in range(1, n + 1):
            target = int(total * i / (n + 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = cap.read()
            if not ok:
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp_path = tmp.name
            tmp.close()
            try:
                cv2.imwrite(tmp_path, frame)
                desc = self._call_vision(tmp_path)
                if desc and desc.strip():
                    ts = target / fps if fps > 0 else 0
                    descs.append(f"[{int(ts // 60):02d}:{int(ts % 60):02d}] {desc.strip()}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        cap.release()
        if fps > 0:
            descs.insert(0, f"时长 {int(total / fps // 60)} 分 {int(total / fps % 60)} 秒")
        return descs

    # ------------------------------------------------------------------
    # 视觉模型调用（qwen3-vl）
    # ------------------------------------------------------------------

    def _call_vision(self, image_path: str) -> str:
        """调用 Ollama 视觉模型描述图片，失败时返回空字符串"""
        if self._http is None:
            self._http = httpx.Client(base_url=self._ollama_base, timeout=180)

        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.warning("读取图片失败 %s: %s", image_path, e)
            return ""

        payload = {
            "model": self._vision_model,
            "messages": [{
                "role": "user",
                "content": "请详细描述这张图片的内容，包括文字、物体、人物、场景、颜色等所有可见信息",
                "images": [b64],
            }],
            "stream": False,
        }
        try:
            resp = self._http.post("/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except httpx.TimeoutException:
            logger.warning("视觉模型超时: %s（图片可能过大）", Path(image_path).name)
            return ""
        except httpx.HTTPStatusError as e:
            logger.warning("视觉模型返回错误 %s: %s", e.response.status_code, Path(image_path).name)
            return ""
        except Exception as e:
            logger.warning("视觉模型调用失败 %s: %s", Path(image_path).name, e)
            self._http.close()
            self._http = None
            return ""

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _resize_image(image_path: str, max_px: int = 1024) -> str:
        try:
            from PIL import Image
        except ImportError:
            return image_path

        img = Image.open(image_path)

        if img.width <= max_px and img.height <= max_px:
            return image_path

        img.thumbnail((max_px, max_px), Image.LANCZOS)

        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(tmp.name, "JPEG", quality=85)
        return tmp.name

    def close(self):
        if self._http:
            self._http.close()
            self._http = None
