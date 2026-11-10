import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app.access_log import hide_query_strings
from app.auth import is_admin, verify_credentials
from app.database import Base, engine
from app.routers import queue, admin, customers
from app.schema_check import require_current_schema

Base.metadata.create_all(bind=engine)
require_current_schema(engine)


hide_query_strings()

app = FastAPI(title="Demo Barbershop")

cors_origins = [origin for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_secret = os.getenv("SESSION_SECRET_KEY")
if not session_secret:
    session_secret = secrets.token_hex(32)
session_https_only = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="phone_assistant_session",
    https_only=session_https_only,
)



@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, error: RequestValidationError):
    """FastAPI's usual 422 body, without `input`: what was typed is never sent back."""
    detail = [
        {"type": item.get("type", "value_error"), "loc": list(item.get("loc", ())), "msg": str(item.get("msg", ""))}
        for item in error.errors()
    ]
    return JSONResponse({"detail": detail}, status_code=422)


templates = Jinja2Templates(directory="app/templates")

app.include_router(queue.router)
app.include_router(queue.admin_router)
app.include_router(admin.public_router)
app.include_router(admin.router)
app.include_router(customers.router)


def _render(request: Request, template_name: str):
    return templates.TemplateResponse(
        template_name, {"request": request, "is_admin": is_admin(request)}
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/checkin")
def checkin_page(request: Request):
    return _render(request, "checkin.html")


class LoginPayload(BaseModel):
    username: str
    password: str


@app.get("/api/me")
def api_me(request: Request):
    return {"is_admin": is_admin(request)}


@app.post("/api/login")
def api_login(request: Request, payload: LoginPayload):
    if verify_credentials(payload.username, payload.password):
        request.session["is_admin"] = True
        return {"is_admin": True}
    raise HTTPException(status_code=401, detail="Invalid username or password.")


@app.post("/api/logout")
def api_logout(request: Request):
    request.session.clear()
    return {"is_admin": False}


# Serve the built React marketing/booking site (frontend/dist, from `npm run build`)
# from this same app/port, so there's a single server for both frontend and backend.
# Any path not already matched by a route above (e.g. /booking, /gallery, static
# assets) falls through to here; unknown paths get index.html so React Router can
# handle client-side routing.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        # The calendar feed is served only by app.calendar_main.
        if full_path.startswith("calendar/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
