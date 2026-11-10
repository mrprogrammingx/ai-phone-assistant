"""Keeps query strings and path tokens out of uvicorn's access log.

The access log prints the full request line, and the booking lookup carries a phone
number in its query string. Everything after `?` is replaced before the line is written.
On the assistant app the path can also hold the secret token, so that is replaced too.
"""
import logging
import os
import re
from urllib.parse import quote

REDACTED = "[redacted]"
_TOKEN_SEGMENT = re.compile(r"(/assistant/t/)[^/?]*", re.IGNORECASE)


def redact_path(path: str) -> str:
    """The token segment always goes. The configured token goes wherever else it appears,
    because a mistyped path can carry it outside the place it belongs."""
    path = _TOKEN_SEGMENT.sub(rf"\g<1>{REDACTED}", path)
    token = os.getenv("SUISSELINE_PATH_TOKEN")
    if token:
        path = path.replace(token, REDACTED).replace(quote(token), REDACTED)
    return path


class _NoQueryStrings(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) >= 3 and isinstance(arguments[2], str):
            path, mark, _query = arguments[2].partition("?")
            if mark:
                record.args = (*arguments[:2], f"{path}?[redacted]", *arguments[3:])
        return True


class _NoPathTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) >= 3 and isinstance(arguments[2], str):
            record.args = (*arguments[:2], redact_path(arguments[2]), *arguments[3:])
        return True


def hide_path_tokens() -> None:
    """Like `hide_query_strings`, for the app that serves the token routes."""
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, _NoPathTokens) for existing in access_log.filters):
        access_log.addFilter(_NoPathTokens())


def hide_query_strings() -> None:
    """Call when an app module is imported. Uvicorn configures logging before it imports
    the app, so the filter stays attached. Calling it twice adds one filter."""
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, _NoQueryStrings) for existing in access_log.filters):
        access_log.addFilter(_NoQueryStrings())
