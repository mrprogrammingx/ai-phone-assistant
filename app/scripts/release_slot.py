"""Manual demo step: offer a freed time to matching waitlist entries.

    python -m app.scripts.release_slot 2026-12-02 14:30

Run locally only. It uses DATABASE_URL, opens no network connection and sends nothing:
a person must contact the customers listed.
"""
import sys
from datetime import date, time

from dotenv import load_dotenv

load_dotenv()

from app.database import Base, SessionLocal, engine
from app.schema_check import require_current_schema
from app.services.catalog import SERVICES
from app.services.waitlist import release_slot

BANNER = "MANUAL CONTACT: nothing was sent. The slot is not reserved."
USAGE = "Usage: python -m app.scripts.release_slot YYYY-MM-DD HH:MM"


def _parse(arguments: list[str]) -> tuple[date, time] | None:
    if len(arguments) != 2:
        return None
    try:
        parsed = date.fromisoformat(arguments[0]), time.fromisoformat(arguments[1])
    except ValueError:
        return None
    return None if parsed[1].tzinfo is not None else parsed


def main(arguments: list[str] | None = None) -> int:
    parsed = _parse(sys.argv[1:] if arguments is None else arguments)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 2

    print(BANNER)
    Base.metadata.create_all(bind=engine)
    require_current_schema(engine)

    with SessionLocal() as db:
        result = release_slot(db, *parsed)

    if result.refused_reason:
        print(f"No offers created: {result.refused_reason}")
    elif not result.offers:
        print("No offers created: no matching waitlist entry can take this time.")

    for offer in result.offers:
        service = SERVICES[offer.service_id]["name"]
        print(f"{offer.name} | {offer.phone} | {service} | offer ends {offer.expires_local:%Y-%m-%d %H:%M}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
