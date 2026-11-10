"""Keeps query strings out of uvicorn's access log.

The access log prints the full request line, and the booking lookup carries a phone
number in its query string. Everything after `?` is replaced before the line is written.
"""
import logging


class _NoQueryStrings(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) >= 3 and isinstance(arguments[2], str):
            path, mark, _query = arguments[2].partition("?")
            if mark:
                record.args = (*arguments[:2], f"{path}?[redacted]", *arguments[3:])
        return True


def hide_query_strings() -> None:
    """Call when an app module is imported. Uvicorn configures logging before it imports
    the app, so the filter stays attached. Calling it twice adds one filter."""
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, _NoQueryStrings) for existing in access_log.filters):
        access_log.addFilter(_NoQueryStrings())
