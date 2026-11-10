"""Reads the envelope Suisse Line wraps around a tool's parameters.

Seen in a capture from chat test mode: the body is a JSON object whose `tool.arguments`
holds the parameters, all as text. Next to them are the call's transcript, the agent's
prompt and greeting, the conversation, the caller's and the called number, and call ids.

Only `tool.arguments` is read. Everything else in the envelope is ignored, so it is never
logged, stored or echoed, and the caller's number never replaces the spoken one.
"""


class NotToolArguments(ValueError):
    """The body has a `tool` key, but no arguments object to read."""


def tool_arguments(body):
    """Returns the parameters a tool should see.

    A body without a `tool` key is already flat (curl, tests, the header routes) and is
    returned unchanged. A body with one must carry `tool.arguments` as an object.
    """
    if not isinstance(body, dict) or "tool" not in body:
        return body
    tool = body["tool"]
    if not isinstance(tool, dict):
        raise NotToolArguments
    arguments = tool.get("arguments")
    if not isinstance(arguments, dict):
        raise NotToolArguments
    return arguments
