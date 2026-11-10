"""Receives Suisse Line's call.completed webhook. The fallback if live tools are unavailable.

Mounted only on the public assistant app. Requests are authenticated by an HMAC-SHA256
signature over the raw body. The real header name and format are unverified.
"""
import hashlib
import hmac
import json
import logging
import os
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.call_requests import (
    notify_staff,
    parse_call_completed,
    record_call,
    staff_recipients,
)

logger = logging.getLogger("barbershop.assistant")

MAX_BODY_BYTES = 64 * 1024
DEFAULT_SIGNATURE_HEADER = "X-Signature"

router = APIRouter(prefix="/assistant/webhook", tags=["webhook"])


def _signature_is_valid(body: bytes, provided: str | None) -> bool:
    secret = os.getenv("SUISSELINE_WEBHOOK_SECRET", "")
    if not secret or not provided:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided.strip().removeprefix("sha256=").encode(), expected.encode())


async def _signed_body(request: Request) -> bytes:
    """Reads at most MAX_BODY_BYTES, then checks the signature over the exact bytes received."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Body too large")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Body too large")

    header = os.getenv("WEBHOOK_SIGNATURE_HEADER") or DEFAULT_SIGNATURE_HEADER
    if not _signature_is_valid(bytes(body), request.headers.get(header)):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return bytes(body)


def _log_outcome(request: Request, status: str, call_id: str | None) -> None:
    """Path, status and call id only. An id that looks unusual is not written out."""
    if call_id is None:
        shown = "-"
    else:
        shown = call_id if re.fullmatch(r"[A-Za-z0-9._:-]+", call_id) else "unprintable"
    logger.info("path=%s status=%s call_id=%s", request.url.path, status, shown)


@router.post("/call-completed")
def call_completed(
    request: Request,
    background: BackgroundTasks,
    body: bytes = Depends(_signed_body),
    db: Session = Depends(get_db),
):
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = parse_call_completed(payload)
    if not parsed.is_call_completed:
        _log_outcome(request, "ignored", parsed.call_id)
        return {"ok": True, "status": "ignored"}

    result = record_call(db, parsed, body)
    status = result.request.status.value
    if result.created:
        background.add_task(
            notify_staff,
            result.request.id,
            status,
            result.request.reason,
            staff_recipients(db),
            result.request.suggested_times,
        )

    _log_outcome(request, status, result.request.call_id)
    return {"ok": True, "status": status}
