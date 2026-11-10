"""Request bodies on the seven token tool routes: a size cap, then the Suisse Line envelope.

Runs inside the token check, so a wrong token is still a plain 404. The header-key routes,
capture mode and the webhook are left alone. A body is never logged or echoed here.
"""
import json

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import PATH_TOKEN_PREFIX
from app.routers import assistant
from app.services.envelope import NotToolArguments, tool_arguments

# Captured bodies were 2.6 to 4 KB and grow with the transcript.
MAX_TOOL_BODY_BYTES = 256 * 1024

TOO_LARGE = "The request is too large."
NOT_ARGUMENTS = "The tool's arguments must be a JSON object."
TOO_DEEP = "The request must be a JSON object containing this tool's fields."

_TOOL_PATHS = frozenset(route.path for route in assistant.tools.routes)


def is_token_tool_path(path: str) -> bool:
    if not path.startswith(PATH_TOKEN_PREFIX):
        return False
    _token, slash, rest = path.removeprefix(PATH_TOKEN_PREFIX).partition("/")
    return bool(slash) and "/" + rest in _TOOL_PATHS


def _length_is_acceptable(scope: Scope) -> bool:
    declared = Headers(scope=scope).get("content-length")
    if declared is None:
        return True  # No length given, so the cap is checked while reading.
    return declared.isascii() and declared.isdigit() and int(declared) <= MAX_TOOL_BODY_BYTES


class ToolBodies:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not is_token_tool_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def refuse(message: str) -> None:
            await assistant._failure(Request(scope), "invalid_input", message)(scope, receive, send)

        if not _length_is_acceptable(scope):
            await refuse(TOO_LARGE)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # Nobody is left to answer.
            body.extend(message.get("body", b""))
            if len(body) > MAX_TOOL_BODY_BYTES:
                await refuse(TOO_LARGE)
                return
            if not message.get("more_body", False):
                break

        try:
            parsed = json.loads(body)
        except RecursionError:
            await refuse(TOO_DEEP)
            return
        except ValueError:
            parsed = None  # Not JSON: the route answers it as it always has.
        try:
            arguments = tool_arguments(parsed)
        except NotToolArguments:
            await refuse(NOT_ARGUMENTS)
            return
        if arguments is not parsed:
            body = json.dumps(arguments).encode()

        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
