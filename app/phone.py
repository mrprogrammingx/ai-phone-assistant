"""Phone numbers are stored in E.164, so one person is one customer however they type it."""
import re
import unicodedata

# The longest text accepted as a typed number, once surrounding space is removed.
MAX_TYPED_PHONE_LENGTH = 40


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Converts a spoken or typed number to E.164. National numbers are assumed Swiss.

    The website, the assistant and the webhook all decide validity here and nowhere
    else, so they cannot disagree. Space around the number is ignored. A line break,
    tab or other control character inside it makes it invalid, as does anything longer
    than a number could be.
    """
    raw = raw.strip()
    if len(raw) > MAX_TYPED_PHONE_LENGTH or any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in raw):
        raise InvalidPhone(raw)
    compact = re.sub(r"[\s().\-/]", "", raw.replace("(0)", ""))
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    elif compact.startswith("0"):
        compact = "+41" + compact[1:]
    elif compact.startswith("41") and len(compact) == 11:
        compact = "+" + compact

    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", compact):
        raise InvalidPhone(raw)
    if compact.startswith("+41") and len(compact) != 12:
        raise InvalidPhone(raw)
    return compact
