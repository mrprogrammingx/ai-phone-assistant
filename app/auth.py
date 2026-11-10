import os
import secrets

from fastapi import HTTPException, Request

def verify_credentials(username: str, password: str) -> bool:
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_password:
        return False
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    return secrets.compare_digest(username, admin_username) and secrets.compare_digest(
        password, admin_password
    )


def is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _presented_assistant_key(request: Request) -> str:
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def require_assistant_key(request: Request) -> None:
    """FastAPI dependency for the phone assistant. Refuses everything if no key is configured."""
    expected = os.getenv("ASSISTANT_API_KEY", "")
    presented = _presented_assistant_key(request)
    if not expected or not secrets.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key")


def require_admin(request: Request) -> None:
    """FastAPI dependency: raises 401 for API routes when not logged in as admin."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Admin login required")
