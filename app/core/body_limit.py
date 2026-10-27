from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from app.core.config import settings


class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, "Request exceeds upload limit")


class BodyLimitMiddleware:
    """Limit the actual streamed request body before multipart parsing spools it to disk."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        maximum = (settings.max_upload_mb + 1) * 1024**2
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > maximum:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            response = JSONResponse(
                {"error": {"code": "upload_too_large", "message": "Request exceeds upload limit"}},
                status_code=413,
            )
            await response(scope, receive, send)
