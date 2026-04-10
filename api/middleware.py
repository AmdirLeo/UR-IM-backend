from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from db.redis_client import db_check_rate_limit, db_get_ttl, get_real_ip
from core.exceptions import BusinessException


class MultiLayerRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(("/docs", "/openapi.json", "/static")):
            return await call_next(request)

        client_ip = get_real_ip(request)

        # 1. 全局层：每秒 20 次
        global_key = f"limit:global:{client_ip}"
        if not await db_check_rate_limit(global_key, limit=20, window=1):
            return JSONResponse(
                status_code=429,
                content={"code": 429, "message": "系统繁忙，请稍后再试（全局限流）"}
            )

        # 2. 单 API 层：每秒 2 次
        api_key = f"limit:api:{path}:{client_ip}"
        if not await db_check_rate_limit(api_key, limit=2, window=1):
            ttl = await db_get_ttl(api_key)
            return JSONResponse(
                status_code=429,
                content={"code": 429, "message": f"该操作过于频繁，请在 {ttl}s 后重试"}
            )

        return await call_next(request)
