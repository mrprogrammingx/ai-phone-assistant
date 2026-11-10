"""The read-only calendar feed, on its own port and local by default.

It mounts the feed route and nothing else: no website, staff API, assistant or API
docs. Run it with:

    uvicorn app.calendar_main:app --host 127.0.0.1 --port 8004
"""
import logging
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.database import engine
from app.routers import calendar_feed
from app.schema_check import require_current_schema

# Uvicorn's access log prints the request path, and this path holds the secret token.
# Uvicorn loads this module after configuring logging, so this setting wins.
_access_log = logging.getLogger("uvicorn.access")
_access_log.handlers.clear()
_access_log.propagate = False
_access_log.disabled = True

logger = logging.getLogger("phone_assistant.calendar")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # This app only reads, so unlike the other two it never creates tables.
    require_current_schema(engine)
    yield


# redirect_slashes is off so that a trailing slash cannot reveal that the route exists.
app = FastAPI(
    title="Demo Barbershop calendar feed",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    redirect_slashes=False,
    lifespan=lifespan,
)
app.include_router(calendar_feed.router)


async def _hide_method_not_allowed(_request: Request, _exc: Exception) -> JSONResponse:
    """A 405 would announce the route just as a 401 would."""
    return JSONResponse({"detail": "Not Found"}, status_code=404)


app.add_exception_handler(405, _hide_method_not_allowed)


def _path_for_log(path: str) -> str:
    """Any path could be a mistyped token, so the real one is never logged."""
    return "/calendar/[redacted]" if path.startswith("/calendar/") else "[other]"


@app.middleware("http")
async def log_timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info("path=%s status=%s duration_ms=%.0f", _path_for_log(request.url.path), response.status_code, duration_ms)
    return response
