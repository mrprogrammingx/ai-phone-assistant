"""GET /calendar/<token>.ics. The token is the secret because calendar apps cannot send headers."""
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.calendar_feed import build_feed

MINIMUM_TOKEN_LENGTH = 24

router = APIRouter()


def _token_is_valid(candidate: str) -> bool:
    """Fails closed: with no usable token configured, nothing is valid."""
    expected = os.getenv("CALENDAR_FEED_TOKEN") or ""
    if len(expected) < MINIMUM_TOKEN_LENGTH:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


@router.get("/calendar/{token}.ics")
def calendar_feed(token: str, db: Session = Depends(get_db)):
    if not _token_is_valid(token):
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(
        content=build_feed(db).encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
