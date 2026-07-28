"""
API 路由定义 —— FastAPI 接口

接口列表：
  GET    /health           → 健康检查 + 模型信息
  POST   /query            → 知识库问答
  POST   /query/clarify    → 歧义检测 / 反问选项（供前端卡片）
  POST   /query/image      → 图片问答（上传图片 + 问题，用 qwen3-vl 视觉模型回答）
  POST   /upload           → 上传文档
  POST   /scan             → 手动触发目录扫描
  GET    /stats            → 知识库统计
  GET    /scan/index       → 文件索引状态
"""
import os
import json
import base64
import hashlib
import shutil
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Response
from fastapi.responses import StreamingResponse

from rag_knowledge.config import Config
from rag_knowledge.ollama_http import async_client as ollama_async_client
from rag_knowledge.ollama_http import client as ollama_client
from rag_knowledge.services.agent_service import load_agents
from rag_knowledge.services.qa_trace import QaTraceStore, set_request_context
from rag_knowledge.models.api import AdminQaDebugResponse, QueryRequest, QueryResponse, UploadResponse
from rag_knowledge.models.api import ClarifyRequest, ClarifyResponse, ClarificationOption, ClarificationOptionFilter
from rag_knowledge.models.api import (
    AdminChunkListResponse,
    AdminChunkUpdateRequest,
    BatchReviewRequest,
    ChunkStatsResponse,
    QaTraceListResponse,
    QaTraceFeedbackRequest,
    ReviewRequest,
    ReviewResponse,
    RebuildRequest,
    ScanResponse,
    StatsResponse,
)
from rag_knowledge.models.api import CrawlRequest, CrawlResponse, BlogPostListResponse, BlogPostItem
from rag_knowledge.services.loader import FileLoader
from rag_knowledge.services.document_profiles import normalize_document_profile
from rag_knowledge.services.query_clarification import QueryClarificationService
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.scanner import DirectoryScanner
from rag_knowledge.services.blog_syncer import BlogPostSyncer
from rag_knowledge.services.blog_crawler import create_crawler, detect_platform
from rag_knowledge.services.chat_storage import ChatStorage
from rag_knowledge.services.chunk_stats import ChunkStatsService
from rag_knowledge.services.chunk_admin import (
    ChunkAdminService,
    DOC_CATEGORIES,
    REVIEW_STATUSES,
    RetrievalRefreshError,
)
from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyService
from rag_knowledge.services.index_cleanup import cleanup_indexed_file
from rag_knowledge.services.query_cache import clear_query_cache
from rag_knowledge.services.rebuild_coordinator import RebuildAlreadyRunningError, RebuildCoordinator
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.knowledge_graph import KnowledgeGraphService
from rag_knowledge.services.product_backbone_preview import (
    ProductBackbonePreviewService,
    ProductBackboneComplexPreviewService,
)
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier, GraphQualityService
from rag_knowledge.services.graph_governance import (
    approve_all_allowed,
    assert_production_apply_allowed,
    filter_approvable_candidate_ids,
)
from rag_knowledge.models.api import (
    LinkTypeEnum,
    EntityCreateRequest,
    EntityCreateResponse,
    EntityUpdateRequest,
    EntityResponse,
    RelationCreateRequest,
    RelationResponse,
    EntityChunkLinkRequest,
    EntityChunkLinkResponse,
    GraphDataResponse,
    GraphEdge,
    GraphNode,
    ProductBackboneEntityRequest,
    ProductBackboneEntityUpdateRequest,
    ProductBackboneRelationRequest,
    EntityChunkDetailResponse,
    GraphAliasCreateRequest,
    GraphAliasItem,
    GraphCandidateBatch,
    GraphCandidateItem,
    GraphCandidateReviewRequest,
    GraphCandidateReviewResponse,
    GraphCandidateApplyResponse,
    GraphQualityResponse,
    UserFeedbackRequest,
    UserFeedbackResponse,
    QualityDashboardResponse,
)
from rag_knowledge.services.quality_service import QualityService

logger = logging.getLogger(__name__)

router = APIRouter()

_scanner: DirectoryScanner | None = None
_rag: RagChain | None = None
_loader: FileLoader | None = None
_store: VectorStore | None = None
_cfg: Config | None = None
_syncer: BlogPostSyncer | None = None
_chat_storage: ChatStorage | None = None

_UPLOAD_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".xls", ".xlsx"}

# 文件魔数 → 扩展名映射
_MAGIC: dict[bytes, set[str]] = {
    b"%PDF": {".pdf"},
    b"PK\x03\x04": {".docx", ".xlsx"},           # DOCX / XLSX 本质是 ZIP
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": {".doc", ".xls"},  # OLE2（旧版 Word / Excel）
}


def init_components(scanner: DirectoryScanner, rag: RagChain, loader: FileLoader, store: VectorStore, cfg: Config):
    """启动时注入各组件实例"""
    global _scanner, _rag, _loader, _store, _cfg, _syncer, _chat_storage
    _scanner = scanner
    _rag = rag
    _loader = loader
    _store = store
    _cfg = cfg
    _syncer = BlogPostSyncer(cfg.blog_publish_dir, cfg.blog_publish_api_url, cfg.data_dir) if cfg.blog_publish_api_url.strip() else None
    _chat_storage = ChatStorage(cfg.data_dir)


def _rebuild_bm25():
    """文档入库后重建 BM25 索引"""
    try:
        from rag_knowledge.services.bm25_store import BM25Store
        BM25Store().rebuild()
    except Exception as e:
        logger.warning("BM25 索引重建失败: %s", e)


def _invalidate_retrieval_caches(reason: str) -> None:
    logger.debug("invalidate retrieval caches | reason=%s", reason)
    clear_query_cache()


def _resolve_chunk_ids(file_paths: list[str] | None, chunk_ids: list[str] | None) -> list[str]:
    """将文件路径和 chunk_id 混合请求解析为最终 chunk_id 列表。"""
    resolved = set(chunk_ids or [])
    if not file_paths or _scanner is None:
        return list(resolved)

    files = _scanner.get_index().get("files", [])
    indexed = {entry.get("file_path"): entry for entry in files}
    for file_path in file_paths:
        entry = indexed.get(file_path)
        if entry and entry.get("chunk_ids"):
            resolved.update(entry["chunk_ids"])
    return list(resolved)


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------

@router.get("/models")
def list_models():
    """获取 Ollama 可用模型列表 + 当前配置，按类型分类"""
    available = []
    try:
        with ollama_client(base_url=_cfg.ollama_base_url, timeout=10) as client:
            resp = client.get("/api/tags")
            available = [
                {"name": m["name"], "type": _classify_model(m["name"])}
                for m in resp.json().get("models", [])
            ]
    except Exception as e:
        logger.warning("获取模型列表失败: %s", e)

    return {
        "models": available,
        "current": {
            "llm": _cfg.llm_model,
            "embedding": _cfg.embedding_model,
            "vision": _cfg.vision_model,
        },
    }


def _classify_model(name: str) -> str:
    """根据模型名推断类型: llm / vision / embedding"""
    n = name.lower()
    if "embedding" in n:
        return "embedding"
    if "vl" in n or "vision" in n:
        return "vision"
    return "llm"


@router.get("/knowledge-bases")
def list_knowledge_bases():
    """获取知识库列表（已发布文章 / 文章附件）"""
    return {"bases": ["全部知识库", "文章附件", "已发布文章"]}


@router.get("/agents")
def list_agents():
    """获取智能体预设列表"""
    return {"agents": load_agents()}


@router.get("/health")
def health():
    """健康检查"""
    return {
        "status": "ok",
        "models": {
            "embedding": _cfg.embedding_model,
            "llm": _cfg.llm_model,
            "vision": _cfg.vision_model,
        },
        "watch_directory": str(_cfg.watch_dir),
    }


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """知识库问答"""
    if not req.question.strip():
        raise HTTPException(400, detail="问题不能为空")

    try:
        set_request_context(path="query")
        history = [h.dict() for h in req.history] if req.history else None
        kb_name = req.kb_name if req.kb_name and req.kb_name != "全部知识库" else None
        doc_category = req.doc_category if req.doc_category and req.doc_category != "全部" else None
        entity_name = (req.entity_name or "").strip() or None
        result = await _rag.aquery(req.question, history,
                                   llm_model=req.llm_model, vision_model=req.vision_model,
                                   kb_name=kb_name, doc_category=doc_category,
                                   entity_name=entity_name,
                                   thinking=req.thinking, web_search=req.web_search,
                                   allow_general_knowledge=req.allow_general_knowledge,
                                   agent_prompt=req.agent_prompt)
        return QueryResponse(answer=result["answer"], source_documents=result["source_documents"])
    except Exception as e:
        logger.error("查询失败: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/query/clarify", response_model=ClarifyResponse)
async def query_clarify(req: ClarifyRequest):
    """检测问题歧义，返回结构化反问选项（供前端渲染卡片）。

    用户选择后，将 option.filter 中的 doc_category / entity_name 带入后续 POST /query。
    """
    if not req.question.strip():
        raise HTTPException(400, detail="问题不能为空")

    doc_category = req.doc_category if req.doc_category and req.doc_category != "全部" else None
    kb_name = req.kb_name if req.kb_name and req.kb_name != "全部知识库" else None
    entity_name = (req.entity_name or "").strip() or None
    result = QueryClarificationService().analyze(
        req.question,
        doc_category=doc_category,
        kb_name=kb_name,
        entity_name=entity_name,
    )
    return ClarifyResponse(
        needs_clarification=result.needs_clarification,
        ask_question=result.ask_question,
        trigger=result.trigger,
        reason=result.reason,
        options=[
            ClarificationOption(
                id=opt.id,
                label=opt.label,
                filter=ClarificationOptionFilter(**opt.filter.to_dict()),
            )
            for opt in result.options
        ],
    )


@router.post("/query/stream")
async def query_stream(req: QueryRequest):
    """
    流式问答 —— SSE (Server-Sent Events)

    客户端通过 EventSource 或 fetch + ReadableStream 消费。

    事件格式：
      data: {"type": "sources", "data": [...]}
      data: {"type": "token",   "data": "..."}
      data: {"type": "final_answer", "data": "..."}  # 可选，最终证据校验改写
      data: {"type": "done"}
    """
    if not req.question.strip():
        raise HTTPException(400, detail="问题不能为空")

    set_request_context(path="query/stream")
    history = [h.dict() for h in req.history] if req.history else None
    kb_name = req.kb_name if req.kb_name and req.kb_name != "全部知识库" else None
    doc_category = req.doc_category if req.doc_category and req.doc_category != "全部" else None
    entity_name = (req.entity_name or "").strip() or None

    async def event_stream():
        async for event in _rag.stream_query(req.question, history,
                                              llm_model=req.llm_model, vision_model=req.vision_model,
                                              kb_name=kb_name, doc_category=doc_category,
                                              entity_name=entity_name,
                                              thinking=req.thinking, web_search=req.web_search,
                                              allow_general_knowledge=req.allow_general_knowledge,
                                              agent_prompt=req.agent_prompt):
            if event.get("type") == "status":
                yield "event: status\n"
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/query/image")
async def query_with_image(
    image: UploadFile = File(...),
    question: str = Form("请详细描述这张图片的内容"),
    vision_model: str = Form(None),
):
    """
    图片问答 —— 上传图片并提问，使用视觉模型回答（流式 SSE）
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(400, detail="只支持图片格式")

    content = await image.read()
    b64 = base64.b64encode(content).decode("utf-8")
    model = vision_model or _cfg.vision_model

    async def event_stream():
        try:
            async with ollama_async_client(base_url=_cfg.ollama_base_url, timeout=120) as client:
                async with client.stream("POST", "/api/chat", json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": question,
                        "images": [b64],
                    }],
                    "stream": True,
                }) as resp:
                    if resp.status_code == 500:
                        yield f"data: {json.dumps({'type': 'token', 'data': '图片处理失败：视觉模型暂时无法处理这张图片，请检查图片格式或大小。'}, ensure_ascii=False)}\n\n"
                        yield "data: {\"type\": \"done\"}\n\n"
                        return
                    resp.raise_for_status()

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield f"data: {json.dumps({'type': 'token', 'data': token}, ensure_ascii=False)}\n\n"
                            if chunk.get("done"):
                                yield "data: {\"type\": \"done\"}\n\n"
                        except json.JSONDecodeError:
                            continue
        except httpx.TimeoutException:
            yield f"data: {json.dumps({'type': 'token', 'data': '处理超时，图片可能过大，请压缩后重试。'}, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
        except Exception as e:
            logger.error("图片问答失败: %s", e)
            yield f"data: {json.dumps({'type': 'token', 'data': f'处理失败：{str(e)}'}, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/upload", response_model=UploadResponse)
def upload(file: UploadFile = File(...), kb_name: str = Form("文章附件"),
           doc_category: str = Form("其他"),
           document_profile: str = Form("section_based")):
    """
    上传文档到监视目录并触发扫描入库

    doc_category: 产品/业务域分类
    """
    if doc_category not in DOC_CATEGORIES:
        raise HTTPException(400, detail=f"doc_category 仅支持 {' / '.join(DOC_CATEGORIES)}")
    try:
        selected_profile = normalize_document_profile(document_profile).value
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in _UPLOAD_EXTS:
        raise HTTPException(400, detail=f"不支持 {suffix}，仅支持 {_UPLOAD_EXTS}")

    # 魔数校验：防止伪装扩展名
    header = file.file.read(16)
    file.file.seek(0)
    if suffix in {".pdf", ".docx", ".doc", ".xls", ".xlsx"}:
        matched = any(header.startswith(m) for m, exts in _MAGIC.items() if suffix in exts)
        if not matched:
            raise HTTPException(400, detail="文件格式校验失败，内容与扩展名不符")

    # 保存到 watch_directory/[upload|已发布文章/upload]/ 下
    save_dir = _cfg.watch_dir / (kb_name if kb_name == "已发布文章" else "") / "upload"
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / file.filename

    # MVP: 将 doc_category 写入扫描器的映射表，扫描时自动应用到所有 chunks
    try:
        rel = str(save_path.relative_to(_cfg.watch_dir))
    except ValueError:
        rel = save_path.name
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if _scanner:
        _scanner.set_doc_category(rel, doc_category)
        _scanner.set_document_profile(rel, selected_profile)

    # 触发扫描器，通过文件哈希去重自动入库
    scan_result = {"new_files": 0, "skipped_files": 0, "errors": 0}
    if _scanner:
        before_chunks = VectorStore().count()
        scan_result = _scanner.scan()
        after_chunks = VectorStore().count()
        chunks_count = max(0, after_chunks - before_chunks)
        _rebuild_bm25()
        _invalidate_retrieval_caches("upload")
    else:
        chunks_count = 0

    return UploadResponse(
        message=f"文件已上传至知识库「{kb_name}」({doc_category}, {selected_profile})",
        chunks_count=chunks_count,
        file_name=file.filename,
        new_files=scan_result["new_files"],
        skipped_files=scan_result["skipped_files"],
        errors=scan_result["errors"],
        decisions=scan_result.get("details"),
    )


@router.post("/scan", response_model=ScanResponse)
def trigger_scan():
    """手动触发目录扫描"""
    if _scanner is None:
        raise HTTPException(503, "扫描器未初始化")
    try:
        r = _scanner.scan()
        _rebuild_bm25()
        _invalidate_retrieval_caches("scan")
        return ScanResponse(
            message=f"新增 {r['new_files']} / 跳过 {r['skipped_files']} / 失败 {r['errors']}",
            new_files=r["new_files"],
            skipped_files=r["skipped_files"],
            errors=r["errors"],
            decisions=r.get("details"),
        )
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.get("/stats", response_model=StatsResponse)
def stats():
    """知识库统计"""
    total = VectorStore().count()
    return StatsResponse(
        total_chunks=total,
        collection_name=_cfg.collection_name,
        watched_directory=str(_cfg.watch_dir),
        file_types=_cfg.watch_file_types,
        scan_interval_minutes=_cfg.scan_interval,
    )


@router.get("/stats/chunks", response_model=ChunkStatsResponse)
def chunk_stats():
    """Chunk 级深度统计。"""
    return ChunkStatsService(cfg=_cfg).build()


@router.get("/scan/index")
def scan_index():
    """文件索引详情"""
    return _scanner.get_index() if _scanner else {"total_files": 0, "files": []}


@router.get("/audit/consistency")
def audit_consistency(source: str | None = None):
    """只读知识库一致性审计。"""
    return KnowledgeBaseConsistencyService().audit(source=source)


@router.get("/admin/chunks", response_model=AdminChunkListResponse)
def admin_chunks(
    review_status: str = "pending",
    doc_category: str = "all",
    filename: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    """List chunks for the admin review workspace."""
    if review_status not in {*REVIEW_STATUSES, "all"}:
        raise HTTPException(400, detail="review_status 仅支持 pending / approved / rejected / all")
    if doc_category not in {*DOC_CATEGORIES, "all"}:
        raise HTTPException(400, detail=f"doc_category 仅支持 {' / '.join(DOC_CATEGORIES)} / all")
    if page < 1:
        raise HTTPException(400, detail="page 必须大于等于 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(400, detail="page_size 必须在 1 到 100 之间")
    return ChunkAdminService().list_chunks(
        review_status=review_status,
        doc_category=doc_category,
        filename=filename,
        page=page,
        page_size=page_size,
    )


@router.patch("/admin/chunks/{chunk_id}", response_model=ReviewResponse)
def update_admin_chunk(chunk_id: str, req: AdminChunkUpdateRequest):
    """Edit review metadata for one chunk."""
    changes = req.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, detail="至少提供 review_status、doc_category、section_title 中的一项")
    if "review_status" in changes and changes["review_status"] not in REVIEW_STATUSES:
        raise HTTPException(400, detail="review_status 仅支持 pending / approved / rejected")
    if "doc_category" in changes and changes["doc_category"] not in DOC_CATEGORIES:
        raise HTTPException(400, detail=f"doc_category 仅支持 {' / '.join(DOC_CATEGORIES)}")

    try:
        updated = ChunkAdminService().update_chunk(chunk_id, changes)
    except RetrievalRefreshError as exc:
        logger.exception("BM25 rebuild failed after admin chunk update")
        raise HTTPException(
            500,
            detail="metadata 已更新，但 BM25 索引重建失败，请重新执行扫描或重建索引。",
        ) from exc
    if not updated:
        raise HTTPException(404, detail="未找到指定的 chunk_id")
    status = changes.get("review_status", "unchanged")
    return ReviewResponse(
        message=f"chunk {chunk_id} 已更新",
        updated_chunks=updated,
        requested_chunks=1,
        status=status,
    )


@router.post("/admin/chunks/batch-review", response_model=ReviewResponse)
def batch_review_admin_chunks(req: BatchReviewRequest):
    """Approve or reject chunks in one operation."""
    if not req.chunk_ids:
        raise HTTPException(400, detail="chunk_ids 不能为空")
    status = req.status.strip().lower()
    if status not in {"approved", "rejected"}:
        raise HTTPException(400, detail="status 仅支持 approved / rejected")
    try:
        response = ChunkAdminService().batch_review(req.chunk_ids, status)
    except RetrievalRefreshError as exc:
        logger.exception("BM25 rebuild failed after batch review")
        raise HTTPException(
            500,
            detail="metadata 已更新，但 BM25 索引重建失败，请重新执行扫描或重建索引。",
        ) from exc
    if not response.updated_chunks:
        raise HTTPException(404, detail="未找到可更新的 chunk_id")
    return response


@router.post("/review/status", response_model=ReviewResponse)
def update_review_status(req: ReviewRequest):
    """批量更新 chunk 审核状态，支持按文件或 chunk_id 提交。"""
    status = req.status.strip().lower()
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(400, detail="status 仅支持 pending / approved / rejected")

    resolved_ids = _resolve_chunk_ids(req.file_paths, req.chunk_ids)
    if not resolved_ids:
        raise HTTPException(400, detail="未找到可更新的 chunk_id")

    try:
        response = ChunkAdminService().batch_review(resolved_ids, status)
    except RetrievalRefreshError as exc:
        logger.exception("BM25 rebuild failed after review status update")
        raise HTTPException(
            status_code=500,
            detail="审核状态已更新，但 BM25 索引重建失败，请重新执行扫描或重建索引。"
        ) from exc
    return response


@router.post("/config/embedding-model")
def set_embedding_model(model: str = Form(...)):
    """
    切换向量模型

    注意：切换后已有向量数据将无法匹配，请立即调用 /rebuild 重建知识库。
    """
    from rag_knowledge.repository.vector_store import VectorStore
    try:
        VectorStore().set_embedding_model(model)
        _invalidate_retrieval_caches("embedding_model_switch")
        logger.info("向量模型已切换: %s", model)
        return {"message": f"向量模型已切换为 {model}，请执行 /rebuild 重建知识库"}
    except Exception as e:
        logger.error("切换向量模型失败: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/rebuild")
def rebuild_knowledge(request: RebuildRequest):
    """
    重建知识库 —— 清空向量数据库 + 文件索引，然后全量重新扫描

    适合场景：
      - 首次部署到新服务器
      - 想完全重新索引所有文件
      - 数据异常需要重置

    注意：此操作不可逆，执行后所有已有问答数据将被清空
    """
    if _scanner is None:
        raise HTTPException(status_code=503, detail="扫描器未初始化，未执行重建")
    try:
        from rag_knowledge.services.scanner import DirectoryScanner

        def _staging_scanner_factory(staged_store, staging_index):
            return DirectoryScanner(
                cfg=_cfg,
                store=staged_store,
                index_path=staging_index,
                refresh_retrieval=False,
                new_chunk_review_status=(
                    "approved" if request.approve_all_chunks else "pending"
                ),
            )

        return RebuildCoordinator(
            cfg=_cfg,
            store=VectorStore(),
            scanner=_scanner,
            consistency_service=KnowledgeBaseConsistencyService(),
            invalidate_retrieval_caches=_invalidate_retrieval_caches,
            rebuild_bm25=_rebuild_bm25,
            staging_scanner_factory=_staging_scanner_factory,
            staging_review_status=(
                "approved" if request.approve_all_chunks else "pending"
            ),
        ).run()
    except RebuildAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("重建失败")
        raise HTTPException(status_code=500, detail=f"重建失败: {exc}") from exc


# ------------------------------------------------------------------
# 博客爬取
# ------------------------------------------------------------------

@router.post("/crawl", response_model=CrawlResponse)
def crawl(req: CrawlRequest):
    """统一爬取入口，根据 URL 自动识别平台"""
    if _cfg is None:
        raise HTTPException(503, detail="配置未初始化")

    url = req.url.strip()
    if not url:
        raise HTTPException(400, detail="URL 不能为空")

    platform = detect_platform(url)
    if not platform:
        raise HTTPException(400, detail=f"不支持的平台链接: {url}")

    try:
        crawler = create_crawler(url, _cfg.blog_crawl_dir, _cfg.crawl_image_dir)
        result = crawler.crawl(url)

        # 只保存到爬取目录，不触发扫描（等待用户点击发布）
        return CrawlResponse(
            title=result["title"],
            source_url=result["source_url"],
            author=result["author"],
            platform=result["platform"],
            publish_date=result.get("publish_date"),
            file_path=result["file_path"],
            message="文章已成功抓取并入库",
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except httpx.HTTPStatusError as e:
        logger.error("抓取失败: %s", e)
        raise HTTPException(502, detail=f"抓取失败，目标网站返回状态码 {e.response.status_code}")
    except Exception as e:
        logger.error("抓取异常: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/blog/posts", response_model=BlogPostListResponse)
def list_blog_posts(page: int = 1, page_size: int = 20, q: str = "", platform: str = ""):
    """列出已保存的博客文章，支持搜索、分页、平台筛选"""
    if _cfg is None:
        raise HTTPException(503, detail="配置未初始化")

    posts_dir = _cfg.blog_crawl_dir
    if not posts_dir.exists():
        return BlogPostListResponse(total=0, page=page, page_size=page_size, total_pages=0, posts=[], posts_dir=str(posts_dir))

    # 递归收集所有 .md 文件
    all_posts: list[BlogPostItem] = []
    for fp in sorted(posts_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        front = _parse_front_matter(fp)
        title = front.get("title", fp.stem)
        author = front.get("author")
        p = front.get("platform", "")

        # 搜索过滤
        if q and q.lower() not in title.lower() and q.lower() not in (author or "").lower():
            continue
        # 平台筛选
        if platform and p != platform:
            continue

        all_posts.append(BlogPostItem(
            filename=fp.name,
            title=title,
            author=author,
            platform=p or None,
            file_path=str(fp),
            file_size=fp.stat().st_size,
            crawled_at=front.get("crawled_at"),
        ))

    total = len(all_posts)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    posts = all_posts[start:start + page_size]

    return BlogPostListResponse(total=total, page=page, page_size=page_size, total_pages=total_pages, posts=posts, posts_dir=str(posts_dir))


@router.get("/blog/posts/{filename}")
def get_blog_post(filename: str):
    """获取单篇博客文章的完整内容"""
    if _cfg is None:
        raise HTTPException(503, detail="配置未初始化")

    # 递归查找匹配的文件
    file_path = None
    for fp in _cfg.blog_crawl_dir.rglob(filename):
        if fp.is_file():
            file_path = fp.resolve()
            break

    if not file_path:
        raise HTTPException(404, detail="文章不存在")

    # 防止路径穿越
    if not str(file_path).startswith(str(_cfg.blog_crawl_dir.resolve())):
        raise HTTPException(400, detail="非法的文件名")

    raw = file_path.read_text(encoding="utf-8")
    return {"filename": filename, "content": raw, "file_path": str(file_path)}


@router.delete("/blog/posts/{filename}")
def delete_blog_post(filename: str):
    """删除博客文章并清理向量库中的对应数据"""
    if _cfg is None:
        raise HTTPException(503, detail="配置未初始化")

    # 递归查找
    fp = None
    for p in _cfg.blog_crawl_dir.rglob(filename):
        if p.is_file():
            fp = p.resolve()
            break
    if not fp:
        raise HTTPException(404, detail="文章不存在")
    if not str(fp).startswith(str(_cfg.blog_crawl_dir.resolve())):
        raise HTTPException(400, detail="非法的文件名")

    # 计算哈希，从 file_index 中查找 chunk_ids 并删除向量
    fhash = _hash_file(str(fp))
    if fhash:
        try:
            cleanup = cleanup_indexed_file(fhash, data_dir=_cfg.data_dir)
            if cleanup.should_rebuild_bm25:
                _rebuild_bm25()
            _invalidate_retrieval_caches("blog_delete")
        except Exception as e:
            logger.warning("清理文件索引失败: %s", e)

    fp.unlink(missing_ok=True)
    logger.info("已删除文件: %s", fp)
    return {"message": f"已删除 {filename}"}


def _hash_file(path: str, buf: int = 65536) -> str | None:
    """计算文件 SHA-256 哈希"""
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(buf):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning("计算哈希失败 %s: %s", path, e)
        return None


@router.post("/blog/publish/{filename}")
def publish_blog_post(filename: str):
    """发布博客文章：调 addRag 接口 → 移入博客文章目录 → 触发扫描入库"""
    if _cfg is None or _scanner is None:
        raise HTTPException(503, detail="服务未初始化")

    # 递归查找文件
    fp = None
    for p in _cfg.blog_crawl_dir.rglob(filename):
        if p.is_file():
            fp = p.resolve()
            break
    if not fp:
        raise HTTPException(404, detail="文章不存在")
    if not str(fp).startswith(str(_cfg.blog_crawl_dir.resolve())):
        raise HTTPException(400, detail="非法的文件名")

    # 读取文件内容
    raw = fp.read_text(encoding="utf-8")
    front = _parse_front_matter(fp)
    title = front.get("title", fp.stem)
    content_md = raw
    sep = raw.find("\n---\n", 3)
    if sep > 0:
        content_md = raw[sep + 5:].strip()
    summary = front.get("description", content_md[:200].strip())

    # 调用 addRag API（未配置则跳过）
    add_rag_url = _cfg.blog_add_rag_url.strip()
    if add_rag_url:
        payload = {
            "id": None,
            "title": title,
            "avatar": "",
            "summary": summary,
            "quantity": 0,
            "content": "",
            "contentMd": content_md,
            "isSecret": 0,
            "isStick": 0,
            "isOriginal": 1,
            "remark": "",
            "keywords": "",
            "categoryName": "运维",
            "isPublish": 1,
            "tags": ["运维"],
            "videoUrl": "[]",
            "txtUrl": "[]",
        }
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(add_rag_url, json=payload)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error("发布失败 %s: %s", filename, e)
            raise HTTPException(502, detail=f"发布接口返回错误: {e.response.status_code}")
        except httpx.TimeoutException:
            raise HTTPException(504, detail="发布接口超时")
        except Exception as e:
            logger.error("发布异常 %s: %s", filename, e)
            raise HTTPException(500, detail=f"发布失败: {str(e)}")
    else:
        logger.info("未配置 addRag API，跳过外部发布")

    # 写入已发布文章目录（用标题哈希作临时 id，同步时会更新为真实 id）
    target_dir = _cfg.blog_publish_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_id = hashlib.md5(title.encode()).hexdigest()[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_front = (
        "---\n"
        f"title: {title}\n"
        f"id: {temp_id}\n"
        f"author: {front.get('author', '')}\n"
        f"source: {front.get('source', '')}\n"
        f"platform: {front.get('platform', '')}\n"
        f"synced_at: {now}\n"
        "---\n\n"
    )
    filename = f"{temp_id}-{_slug(title)}.md"
    target = target_dir / filename
    target.write_text(new_front + content_md, encoding="utf-8")
    fp.unlink()  # 删除爬取目录的原文件
    scan_result = _scanner.scan()
    _rebuild_bm25()
    _invalidate_retrieval_caches("blog_publish")

    logger.info("文章已发布: %s → %s", filename, target)
    return {
        "message": f"文章「{title}」已发布",
        "new_files": scan_result["new_files"],
        "skipped_files": scan_result["skipped_files"],
        "errors": scan_result["errors"],
    }


@router.post("/blog/sync")
def sync_published_posts():
    """同步博客发布系统的已发布文章到本地并触发扫描入库"""
    if _syncer is None or _cfg is None:
        raise HTTPException(503, detail="同步器未初始化")

    try:
        result = _syncer.sync()
        if _scanner:
            scan_result = _scanner.scan()
            _rebuild_bm25()
            _invalidate_retrieval_caches("blog_sync")
            result["scan"] = {k: scan_result[k] for k in ("new_files", "skipped_files", "errors")}
        parts = []
        if result["new"]: parts.append(f"新增 {result['new']}")
        if result["updated"]: parts.append(f"更新 {result['updated']}")
        if result["deleted"]: parts.append(f"删除 {result['deleted']}")
        if result["skipped"]: parts.append(f"跳过 {result['skipped']}")
        result["message"] = "同步完成，" + "、".join(parts) if parts else "无变化"
        return result
    except Exception as e:
        logger.error("同步失败: %s", e)
        raise HTTPException(500, detail=str(e))


# ------------------------------------------------------------------
# 聊天记录（服务端持久化）
# ------------------------------------------------------------------

@router.get("/chat/history")
def get_chat_history(x_device_fingerprint: str = Header(...)):
    """获取用户聊天记录"""
    if _chat_storage is None:
        raise HTTPException(503, detail="聊天记录服务未初始化")
    data = _chat_storage.load(x_device_fingerprint)
    if data is None:
        return {"messages": []}
    return {"messages": data["messages"]}


@router.put("/chat/history")
def save_chat_history(body: dict, x_device_fingerprint: str = Header(...)):
    """保存用户聊天记录"""
    if _chat_storage is None:
        raise HTTPException(503, detail="聊天记录服务未初始化")
    messages = body.get("messages", [])
    _chat_storage.save(x_device_fingerprint, messages)
    return {"message": "ok"}


@router.delete("/chat/history")
def delete_chat_history(x_device_fingerprint: str = Header(...)):
    """删除用户聊天记录"""
    if _chat_storage is None:
        raise HTTPException(503, detail="聊天记录服务未初始化")
    _chat_storage.delete(x_device_fingerprint)
    return {"message": "已删除"}


_FILENAME_SAFE = re.compile(r"[^一-鿿\w\-]", re.UNICODE)


def _slug(title: str, max_len: int = 40) -> str:
    safe = _FILENAME_SAFE.sub("_", title).strip("_")
    safe = re.sub(r"_+", "_", safe)
    return safe[:max_len].rstrip("_") if safe else "untitled"


def _parse_front_matter(file_path: Path) -> dict:
    """只读文件头解析 YAML front-matter，避免加载全文"""
    meta: dict[str, str] = {}
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
            if first.strip() != "---":
                return meta
            for line in f:
                if line.strip() == "---":
                    break
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip()
    except Exception:
        pass
    return meta


# =====================================================================
# 知识图谱 (Knowledge Graph) APIs
# =====================================================================

def _effective_candidate_batch_status(batch: dict, candidates: list[dict]) -> str:
    non_diagnostics = [item for item in candidates if item["candidate_kind"] != "diagnostic"]
    if non_diagnostics and all(item["status"] == "applied" for item in non_diagnostics):
        return "applied"
    return batch["status"]


def _serialize_candidate_batch(batch: dict, db: RelationalDB) -> GraphCandidateBatch:
    candidates = db.list_extraction_candidates(batch["id"])
    effective_status = _effective_candidate_batch_status(batch, candidates)
    stats = json.loads(batch.get("stats_json") or "{}")
    stats.setdefault("total", len(candidates))
    for candidate_status in ("pending", "approved", "rejected", "applied"):
        stats.setdefault(candidate_status, sum(1 for item in candidates if item["status"] == candidate_status))
    return GraphCandidateBatch(
        id=batch["id"],
        mode=batch["mode"],
        status=effective_status,
        created_at=batch["created_at"],
        reviewed_at=batch.get("reviewed_at") or None,
        applied_at=batch.get("applied_at") or None,
        error_text=batch.get("error_text") or None,
        filters=json.loads(batch.get("filters_json") or "{}"),
        stats=stats,
    )


def _sync_batch_status_after_review(db: RelationalDB, batch_id: str) -> str:
    candidates = db.list_extraction_candidates(batch_id)
    non_diagnostics = [item for item in candidates if item["candidate_kind"] != "diagnostic"]
    if non_diagnostics and all(item["status"] == "applied" for item in non_diagnostics):
        db.set_extraction_batch_status(batch_id, "applied")
        return "applied"
    if non_diagnostics and all(item["status"] in {"approved", "applied"} for item in non_diagnostics):
        db.set_extraction_batch_status(batch_id, "approved")
        return "approved"
    if candidates and all(item["status"] in {"approved", "rejected", "applied"} for item in candidates):
        db.set_extraction_batch_status(batch_id, "rejected")
        return "rejected"
    return (db.get_extraction_batch(batch_id) or {}).get("status", "draft")

@router.get("/admin/knowledge_graph/data", response_model=GraphDataResponse)
def get_graph_data(doc_category: Optional[str] = None):
    """获取知识图谱的节点和边"""
    try:
        return KnowledgeGraphService().list_graph_data(doc_category=doc_category)
    except Exception as e:
        logger.error("Failed to list graph data: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/qa-debug", response_model=AdminQaDebugResponse)
async def admin_qa_debug(req: QueryRequest):
    """Run one request with its transient EvidencePack for administration/debugging."""
    if not req.question.strip():
        raise HTTPException(400, detail="问题不能为空")
    try:
        set_request_context(path="qa-debug")
        history = [h.dict() for h in req.history] if req.history else None
        kb_name = req.kb_name if req.kb_name and req.kb_name != "全部知识库" else None
        doc_category = req.doc_category if req.doc_category and req.doc_category != "全部" else None
        entity_name = (req.entity_name or "").strip() or None
        result = await _rag.aquery(
            req.question, history, llm_model=req.llm_model, vision_model=req.vision_model,
            kb_name=kb_name, doc_category=doc_category, entity_name=entity_name,
            thinking=req.thinking,
            web_search=req.web_search, allow_general_knowledge=req.allow_general_knowledge,
            agent_prompt=req.agent_prompt, include_evidence=True,
        )
        return AdminQaDebugResponse(
            answer=result["answer"], source_documents=result["source_documents"],
            evidence_chain=result.get("evidence_chain") or {},
            trace_id=result.get("trace_id"),
        )
    except Exception as e:
        logger.error("问答调试失败: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/qa-debug/stream")
async def admin_qa_debug_stream(req: QueryRequest):
    """SSE debug run: emit pipeline stage payloads (plan/rewrite/retrieval) as they finish."""
    if not req.question.strip():
        raise HTTPException(400, detail="问题不能为空")

    set_request_context(path="qa-debug")
    history = [h.dict() for h in req.history] if req.history else None
    kb_name = req.kb_name if req.kb_name and req.kb_name != "全部知识库" else None
    doc_category = req.doc_category if req.doc_category and req.doc_category != "全部" else None
    entity_name = (req.entity_name or "").strip() or None

    async def event_stream():
        async for event in _rag.stream_query(
            req.question,
            history,
            llm_model=req.llm_model,
            vision_model=req.vision_model,
            kb_name=kb_name,
            doc_category=doc_category,
            entity_name=entity_name,
            thinking=req.thinking,
            web_search=req.web_search,
            allow_general_knowledge=req.allow_general_knowledge,
            agent_prompt=req.agent_prompt,
            pipeline_events=True,
            path="qa-debug",
        ):
            if event.get("type") == "status":
                yield "event: status\n"
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/admin/qa-traces", response_model=QaTraceListResponse)
def list_qa_traces(
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    errors_only: bool = False,
):
    """List persisted QA pipeline traces for the admin monitor."""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    try:
        return QaTraceListResponse(**QaTraceStore().list(
            limit=limit, offset=offset, q=q, errors_only=errors_only,
        ))
    except Exception as e:
        logger.error("列出 qa traces 失败: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/qa-traces/{trace_id}")
def get_qa_trace(trace_id: str):
    """Return one full QA pipeline trace."""
    payload = QaTraceStore().get(trace_id)
    if not payload:
        raise HTTPException(404, detail="trace 不存在")
    return payload


@router.delete("/admin/qa-traces/{trace_id}")
def delete_qa_trace(trace_id: str):
    """Delete one persisted QA pipeline trace."""
    ok = QaTraceStore().delete(trace_id)
    if not ok:
        raise HTTPException(404, detail="trace 不存在")
    return {"ok": True, "trace_id": trace_id}


@router.post("/admin/qa-traces/{trace_id}/feedback")
def update_qa_trace_feedback(trace_id: str, req: QaTraceFeedbackRequest):
    """Update user feedback (useful/unuseful) for a persisted QA trace."""
    ok = QaTraceStore().update_feedback(trace_id, req.feedback)
    if not ok:
        raise HTTPException(404, detail="trace 不存在")
    return {"ok": True, "trace_id": trace_id, "feedback": req.feedback}


@router.get("/admin/knowledge_graph/product_backbone_preview", response_model=GraphDataResponse)
def get_product_backbone_preview():
    """Return the unconfirmed product backbone preview graph without touching KG tables."""
    try:
        return ProductBackbonePreviewService().list_graph_data()
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to list product backbone preview: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/knowledge_graph/product_backbone_preview_complex", response_model=GraphDataResponse)
def get_product_backbone_preview_complex():
    """Return the unconfirmed complex detail product backbone preview graph without touching KG tables."""
    try:
        return ProductBackboneComplexPreviewService().list_graph_data()
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to list product backbone complex preview: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/knowledge_graph/product_backbone_preview/entities", response_model=GraphNode, status_code=201)
def create_product_backbone_entity(req: ProductBackboneEntityRequest):
    """Create a product backbone preview entity in the JSON seed only."""
    try:
        return ProductBackbonePreviewService().create_entity(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create product backbone entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.patch("/admin/knowledge_graph/product_backbone_preview/entities/{entity_id}", response_model=GraphNode)
def update_product_backbone_entity(entity_id: str, req: ProductBackboneEntityUpdateRequest):
    """Update a product backbone preview entity in the JSON seed only."""
    changes = req.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, detail="at least one product backbone entity field is required")
    try:
        return ProductBackbonePreviewService().update_entity(entity_id, changes)
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to update product backbone entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.delete("/admin/knowledge_graph/product_backbone_preview/entities/{entity_id}")
def delete_product_backbone_entity(entity_id: str):
    """Delete a product backbone preview entity and cascade its JSON relations."""
    try:
        ProductBackbonePreviewService().delete_entity(entity_id)
        return {"success": True, "message": "product backbone entity deleted"}
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to delete product backbone entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/knowledge_graph/product_backbone_preview/relations", response_model=GraphEdge, status_code=201)
def create_product_backbone_relation(req: ProductBackboneRelationRequest):
    """Create a product backbone preview relation in the JSON seed only."""
    try:
        return ProductBackbonePreviewService().create_relation(req.model_dump())
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create product backbone relation: %s", e)
        raise HTTPException(500, detail=str(e))


@router.delete("/admin/knowledge_graph/product_backbone_preview/relations/{relation_id}")
def delete_product_backbone_relation(relation_id: str):
    """Delete a product backbone preview relation from the JSON seed only."""
    try:
        ProductBackbonePreviewService().delete_relation(relation_id)
        return {"success": True, "message": "product backbone relation deleted"}
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.error("Failed to delete product backbone relation: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/knowledge_graph/entities/{entity_id}/aliases", response_model=list[GraphAliasItem])
def list_entity_aliases(entity_id: str):
    """获取实体别名列表。"""
    try:
        return KnowledgeGraphService().list_entity_aliases(entity_id)
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.error("Failed to list aliases: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/knowledge_graph/entities/{entity_id}/aliases", response_model=GraphAliasItem, status_code=201)
def create_entity_alias(entity_id: str, req: GraphAliasCreateRequest, response: Response):
    """为实体创建别名。"""
    try:
        res = KnowledgeGraphService().create_entity_alias(
            entity_id=entity_id,
            alias=req.alias,
            confidence=req.confidence,
            evidence_text=req.evidence_text,
            source_chunk_id=req.source_chunk_id,
            review_status=req.review_status,
        )
        if res.created is False:
            response.status_code = 200
        return res
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create alias: %s", e)
        raise HTTPException(500, detail=str(e))


@router.delete("/admin/knowledge_graph/aliases/{alias_id}")
def delete_alias(alias_id: str):
    """删除实体别名。"""
    try:
        KnowledgeGraphService().delete_alias(alias_id)
        return {"success": True, "message": "Alias 删除成功"}
    except Exception as e:
        logger.error("Failed to delete alias: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/knowledge_graph/entities", response_model=EntityCreateResponse, status_code=201)
def create_entity(req: EntityCreateRequest, response: Response):
    """创建实体"""
    try:
        res = KnowledgeGraphService().create_entity(
            name=req.name,
            entity_type=req.entity_type,
            doc_category=req.doc_category,
            canonical_name=req.canonical_name or "",
            description=req.description or "",
            properties_json=req.properties_json or "{}",
            confidence=req.confidence,
            review_status=req.review_status
        )
        if not res.created:
            response.status_code = 200
        return res
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(409, detail=str(e))
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.patch("/admin/knowledge_graph/entities/{entity_id}", response_model=EntityResponse)
def update_entity(entity_id: str, req: EntityUpdateRequest):
    """更新实体属性"""
    changes = req.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, detail="至少提供要修改的一个属性")
    try:
        return KnowledgeGraphService().update_entity(
            entity_id=entity_id,
            name=req.name,
            entity_type=req.entity_type,
            doc_category=req.doc_category,
            canonical_name=req.canonical_name,
            description=req.description,
            properties_json=req.properties_json,
            confidence=req.confidence,
            review_status=req.review_status
        )
    except KeyError:
        raise HTTPException(404, detail="未找到指定的实体")
    except ValueError as e:
        raise HTTPException(409, detail=str(e))
    except Exception as e:
        logger.error("Failed to update entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.delete("/admin/knowledge_graph/entities/{entity_id}")
def delete_entity(entity_id: str):
    """级联删除实体"""
    try:
        KnowledgeGraphService().delete_entity(entity_id)
        return {"success": True, "message": "实体删除成功"}
    except Exception as e:
        logger.error("Failed to delete entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/knowledge_graph/relations", response_model=RelationResponse, status_code=201)
def create_relation(req: RelationCreateRequest, response: Response):
    """创建实体关系"""
    try:
        res = KnowledgeGraphService().create_relation(
            source_id=req.source_id,
            target_id=req.target_id,
            relation_type=req.relation_type,
            properties_json=req.properties_json or "{}",
            confidence=req.confidence,
            evidence_text=req.evidence_text or "",
            source_chunk_id=req.source_chunk_id or "",
            review_status=req.review_status
        )
        if res.created is False:
            response.status_code = 200
        return res
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(409, detail=str(e))
        raise HTTPException(400, detail=str(e))
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.error("Failed to create relation: %s", e)
        raise HTTPException(500, detail=str(e))


@router.delete("/admin/knowledge_graph/relations/{relation_id}")
def delete_relation(relation_id: str):
    """删除实体关系"""
    try:
        KnowledgeGraphService().delete_relation(relation_id)
        return {"success": True, "message": "关系删除成功"}
    except Exception as e:
        logger.error("Failed to delete relation: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/knowledge_graph/entities/{entity_id}/chunks", response_model=EntityChunkLinkResponse, status_code=201)
def link_entity_chunk(entity_id: str, req: EntityChunkLinkRequest, response: Response):
    """关联实体与知识块"""
    try:
        res = KnowledgeGraphService().link_entity_chunk(
            entity_id=entity_id,
            chunk_id=req.chunk_id,
            link_type=req.link_type or LinkTypeEnum.primary
        )
        if not res.created:
            response.status_code = 200
        return res
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.error("Failed to link entity to chunk: %s", e)
        raise HTTPException(500, detail=str(e))


@router.delete("/admin/knowledge_graph/entities/{entity_id}/chunks/{chunk_id}")
def unlink_entity_chunk(entity_id: str, chunk_id: str):
    """移除实体与知识块的关联"""
    try:
        KnowledgeGraphService().unlink_entity_chunk(entity_id, chunk_id)
        return {"success": True, "message": "关联删除成功"}
    except Exception as e:
        logger.error("Failed to unlink entity and chunk: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/knowledge_graph/entities/{entity_id}/chunks", response_model=list[EntityChunkDetailResponse])
def list_entity_chunks(entity_id: str):
    """获取实体关联的所有知识块列表"""
    try:
        return KnowledgeGraphService().list_entity_chunks(entity_id)
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.error("Failed to list chunks for entity: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/graph-candidates/batches", response_model=list[GraphCandidateBatch])
def list_graph_candidate_batches(status: Optional[str] = None):
    """列出图谱候选批次。"""
    try:
        db = RelationalDB()
        batches = [_serialize_candidate_batch(batch, db) for batch in db.list_extraction_batches("")]
        if status:
            batches = [batch for batch in batches if batch.status == status]
        return batches
    except Exception as e:
        logger.error("Failed to list graph candidate batches: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/graph-candidates/batches/{batch_id}/candidates", response_model=list[GraphCandidateItem])
def list_graph_candidate_items(batch_id: str, status: Optional[str] = None):
    """列出批次候选明细。"""
    try:
        db = RelationalDB()
        batch = db.get_extraction_batch(batch_id)
        if not batch:
            raise HTTPException(404, detail="Batch not found")
        return [
            GraphCandidateItem(
                id=item["id"],
                batch_id=item["batch_id"],
                candidate_kind=item["candidate_kind"],
                status=item["status"],
                payload=item["payload"],
                evidence_text=item.get("evidence_text") or None,
                source_chunk_id=item.get("source_chunk_id") or None,
                rejection_reason=item.get("rejection_reason") or None,
                reviewed_at=item.get("reviewed_at") or None,
                applied_at=item.get("applied_at") or None,
                applied_target_id=item.get("applied_target_id") or None,
                created_at=item["created_at"],
            )
            for item in db.list_extraction_candidates(batch_id, status or "")
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list graph candidates: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/graph-candidates/batches/{batch_id}/review", response_model=GraphCandidateReviewResponse)
def review_graph_candidates(batch_id: str, req: GraphCandidateReviewRequest):
    """审批图谱候选。"""
    try:
        db = RelationalDB()
        batch = db.get_extraction_batch(batch_id)
        if not batch:
            raise HTTPException(404, detail="Batch not found")

        pending = db.list_extraction_candidates(batch_id, "pending")
        candidate_ids = {item["id"] for item in pending}
        diagnostic_ids = {item["id"] for item in pending if item["candidate_kind"] == "diagnostic"}

        updated = 0
        reject_ids = [cid for cid in req.reject_ids if cid in candidate_ids]
        if reject_ids:
            updated += db.review_extraction_candidates(batch_id, reject_ids, "rejected", req.reason or "")

        if req.approve_all:
            allowed, reason = approve_all_allowed(batch, pending)
            if not allowed:
                raise HTTPException(400, detail=reason)
            approve_ids = sorted(candidate_ids - diagnostic_ids - set(reject_ids))
        else:
            approve_ids = [
                cid for cid in req.approve_ids
                if cid in candidate_ids and cid not in diagnostic_ids and cid not in set(reject_ids)
            ]
        if approve_ids:
            safe_ids, unsafe_ids = filter_approvable_candidate_ids(
                approve_ids,
                pending,
                batch=batch,
                approve_kind=req.approve_kind,
                explicit_ids=not req.approve_all,
            )
            if unsafe_ids and req.approve_all:
                raise HTTPException(400, detail=f"approve-all rejected unsafe candidates: {len(unsafe_ids)}")
            updated += db.review_extraction_candidates(batch_id, safe_ids, "approved", "")

        batch_status = _sync_batch_status_after_review(db, batch_id)
        return GraphCandidateReviewResponse(
            batch_id=batch_id,
            updated_candidates=updated,
            batch_status=batch_status,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to review graph candidates: %s", e)
        raise HTTPException(500, detail=str(e))


@router.post("/admin/graph-candidates/batches/{batch_id}/apply", response_model=GraphCandidateApplyResponse)
def apply_graph_candidates(batch_id: str):
    """应用已批准的图谱候选。"""
    try:
        db = RelationalDB()
        batch = db.get_extraction_batch(batch_id)
        if not batch:
            raise HTTPException(404, detail="Batch not found")
        if batch["status"] != "approved":
            raise HTTPException(400, detail="Only approved batches can be applied")
        assert_production_apply_allowed()
        approved_count = len(db.list_extraction_candidates(batch_id, "approved"))
        audit = GraphCandidateApplier(db).apply(batch_id, operator="api")
        return GraphCandidateApplyResponse(
            batch_id=batch_id,
            status="applied",
            applied_candidates=approved_count,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.error("Failed to apply graph candidates: %s", e)
        raise HTTPException(500, detail=str(e))


@router.get("/admin/graph-candidates/batches/{batch_id}/quality", response_model=GraphQualityResponse)
def inspect_graph_candidate_batch(batch_id: str):
    """查看批次质量检查结果。"""
    try:
        db = RelationalDB()
        batch = db.get_extraction_batch(batch_id)
        if not batch:
            raise HTTPException(404, detail="Batch not found")
        report = GraphQualityService(db).inspect_batch(batch_id)
        return GraphQualityResponse(
            ok=report.ok,
            errors=report.errors,
            warnings=report.warnings,
            stats=report.stats,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to inspect graph candidate batch: %s", e)
        raise HTTPException(500, detail=str(e))


# ------------------------------------------------------------------
# 质量控制仪表盘与反馈闭环 (Quality Control & Feedback Loop)
# ------------------------------------------------------------------

@router.get("/quality/dashboard", response_model=QualityDashboardResponse)
def get_quality_dashboard():
    """获取质量控制仪表盘汇总数据与预警列表"""
    try:
        service = QualityService(store=_store)
        data = service.get_dashboard_data()
        return QualityDashboardResponse(**data)
    except Exception as e:
        logger.error("获取质量仪表盘数据失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback", response_model=UserFeedbackResponse)
def submit_user_feedback(req: UserFeedbackRequest):
    """提交用户有用/无用反馈并自动触发差评重审闭环"""
    try:
        service = QualityService(store=_store)
        res = service.process_user_feedback(
            user_id=req.user_id,
            query_text=req.query_text,
            answer_text=req.answer_text,
            referenced_chunk_ids=req.referenced_chunk_ids,
            rating=req.rating,
            reason=req.reason,
            trace_id=req.trace_id,
        )
        return UserFeedbackResponse(
            feedback_id=res["feedback_id"],
            rating=res["rating"],
            triggered_chunks=res["triggered_chunks"],
            message=f"已成功提交 {req.rating} 反馈",
        )
    except Exception as e:
        logger.error("提交用户反馈失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/quality/detect-duplicates")
def detect_duplicate_chunks():
    """手动触发 SimHash 文本重复块检测"""
    try:
        service = QualityService(store=_store)
        duplicates = service.detect_duplicate_chunks(similarity_threshold=0.95)
        return {
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
        }
    except Exception as e:
        logger.error("检测重复块失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
