"""
请求日志中间件 —— 记录每次 API 调用的方法、路径、状态码和耗时
"""
import time
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from rag_knowledge.services.qa_trace import set_request_context

logger = logging.getLogger("api")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """记录每次请求的耗时和结果"""

    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        method = request.method
        path = request.url.path
        request.state.request_id = request_id
        set_request_context(request_id=request_id)

        # 不记录静态资源和健康检查
        if path in ("/health", "/favicon.ico"):
            return await call_next(request)

        start = time.time()
        content_length = request.headers.get("content-length", "?")

        # 记录请求开始
        logger.info("[%s] >>> %s %s | size=%s", request_id, method, path, content_length)

        try:
            response = await call_next(request)
        except Exception as e:
            elapsed = time.time() - start
            logger.error("[%s] <<< %s %s | ERROR: %s | %.3fs",
                         request_id, method, path, e, elapsed)
            raise

        elapsed = time.time() - start
        status = response.status_code
        response.headers["X-Request-Id"] = request_id

        if status >= 500:
            logger.error("[%s] <<< %s %s | %d | %.3fs", request_id, method, path, status, elapsed)
        elif status >= 400:
            logger.warning("[%s] <<< %s %s | %d | %.3fs", request_id, method, path, status, elapsed)
        else:
            logger.info("[%s] <<< %s %s | %d | %.3fs", request_id, method, path, status, elapsed)

        return response
