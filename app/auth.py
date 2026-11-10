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


MINIMUM_PATH_TOKEN_LENGTH = 32
PATH_TOKEN_PREFIX = "/assistant/t/"


def path_token_is_valid(candidate: str) -> bool:
    """Fails closed: with no usable token configured, nothing is valid."""
    expected = os.getenv("SUISSELINE_PATH_TOKEN") or ""
    if len(expected) < MINIMUM_PATH_TOKEN_LENGTH:
        return False
    return secrets.compare_digest(candidate.encode(), expected.encode())


def require_path_token(token: str) -> None:
    """FastAPI dependency for the token routes. 404, so they do not announce themselves."""
    if not path_token_is_valid(token):
        raise HTTPException(status_code=404, detail="Not Found")


def require_admin(request: Request) -> None:
    """FastAPI dependency: raises 401 for API routes when not logged in as admin."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Admin login required")
