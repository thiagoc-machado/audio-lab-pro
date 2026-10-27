import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.core.config import settings
from app.core.body_limit import BodyLimitMiddleware
from app.core.exceptions import AudioError
from app.core.logging import configure_logging
from app.services.storage import initialize
from app.api.routes import router

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(
    title="Audio Lab Pro",
    version="1.0.0",
    debug=settings.debug,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts.split(","))
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
app.include_router(router)
app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def protections(request: Request, call_next):
    start = time.monotonic()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if (origin and origin not in settings.allowed_origins.split(",")) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            return JSONResponse(
                {"error": {"code": "origin_rejected", "message": "Request origin is not allowed"}},
                status_code=403,
            )
        length = request.headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > (settings.max_upload_mb + 1) * 1024**2):
            return JSONResponse(
                {"error": {"code": "upload_too_large", "message": "Request exceeds upload limit"}},
                status_code=413,
            )
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            "Cache-Control": "no-store" if request.url.path.startswith("/api/") else "no-cache",
        }
    )
    logger.info(
        "%s %s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        (time.monotonic() - start) * 1000,
    )
    return response


@app.exception_handler(AudioError)
async def audio_error(request, exc):
    return JSONResponse({"error": {"code": "audio_error", "message": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {
            "error": {
                "code": "validation_error",
                "message": "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
            }
        },
        status_code=422,
    )


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse(
        {"error": {"code": "http_error", "message": str(exc.detail)}}, status_code=exc.status_code
    )


@app.exception_handler(Exception)
async def unexpected(request, exc):
    logger.exception("Unhandled request failure")
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "An unexpected error occurred. Check server logs."}},
        status_code=500,
    )


@app.get("/", include_in_schema=False)
@app.get("/history", include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html", context={"history": request.url.path == "/history"}
    )


@app.get("/docs", include_in_schema=False)
def docs(request: Request):
    return templates.TemplateResponse(request=request, name="docs.html", context={})
