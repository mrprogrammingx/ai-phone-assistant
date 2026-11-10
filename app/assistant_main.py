"""The only application exposed to the public tunnel.

It mounts the assistant router and nothing else: no website, staff API, login or
API docs. Run it on its own port with:

    uvicorn app.assistant_main:app --port 8003
"""
import logging
import time

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.access_log import hide_path_tokens, hide_query_strings, redact_path
from app.auth import PATH_TOKEN_PREFIX, path_token_is_valid
from app.database import Base, engine
from app.routers import assistant, webhook
from app.schema_check import require_current_schema

Base.metadata.create_all(bind=engine)
require_current_schema(engine)
hide_query_strings()
hide_path_tokens()

logger = logging.getLogger("barbershop.assistant")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

app = FastAPI(title="Demo Barbershop assistant API", docs_url=None, redoc_url=None, openapi_url=None)
assistant.install_error_handlers(app)
app.include_router(assistant.router)
app.include_router(assistant.token_router)
app.include_router(webhook.router)


@app.middleware("http")
async def hide_token_routes_from_wrong_tokens(request: Request, call_next):
    """The route's dependency alone is not enough. An unreadable body, a wrong method and
    a trailing slash are all answered before dependencies run, and would reveal the route."""
    path = request.scope["path"]
    if path.startswith(PATH_TOKEN_PREFIX):
        if not path_token_is_valid(path.removeprefix(PATH_TOKEN_PREFIX).partition("/")[0]):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


# Added last, so it runs first and also times the refusals above.
@app.middleware("http")
async def log_timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info("path=%s status=%s duration_ms=%.0f", redact_path(request.scope["path"]), response.status_code, duration_ms)
    return response
