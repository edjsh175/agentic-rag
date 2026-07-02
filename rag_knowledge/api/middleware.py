"""
请求日志中间件 —— 记录每次 API 调用的方法、路径、状态码和耗时
"""
import time
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("api")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """记录每次请求的耗时和结果"""

    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        method = request.method
        path = request.url.path
        query = str(request.url.query) if request.url.query else ""

        # 不记录静态资源和健康检查
        if path in ("/health", "/favicon.ico"):
            return await call_next(request)

        start = time.time()
        body_preview = ""
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

        if status >= 500:
            logger.error("[%s] <<< %s %s | %d | %.3fs", request_id, method, path, status, elapsed)
        elif status >= 400:
            logger.warning("[%s] <<< %s %s | %d | %.3fs", request_id, method, path, status, elapsed)
        else:
            logger.info("[%s] <<< %s %s | %d | %.3fs", request_id, method, path, status, elapsed)

        return response
