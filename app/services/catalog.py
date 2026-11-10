"""The services the shop offers, from the file the website also reads."""
import json
from pathlib import Path

_catalog = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "services.json").read_text(encoding="utf-8")
)
CURRENCY = _catalog["currency"]
SERVICES = {service["id"]: service for service in _catalog["services"]}


def staff_note(service: dict) -> str:
    """Text for staff to read on the dashboard and in email. The code never reads it back."""
    return f"Service: {service['name']} ({service['duration_minutes']} min)"
