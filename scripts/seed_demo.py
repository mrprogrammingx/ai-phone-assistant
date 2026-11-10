#!/usr/bin/env python3
"""Fills a brand-new database with fictional demo data.

    DATABASE_URL=sqlite:////tmp/demo.db venv/bin/python scripts/seed_demo.py

Refuses to run unless DATABASE_URL names a new or empty SQLite file. It never reads
.env, so it cannot pick up a real database by accident.
"""
import os
import sys
from datetime import time, timedelta
from pathlib import Path

SQLITE_PREFIX = "sqlite:///"


def refusal() -> str | None:
    """Why this database must not be seeded, or None if it is safe."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return "DATABASE_URL is not set."
    if "barbershop.db" in url.lower():
        return "DATABASE_URL names barbershop.db, which may hold real data."
    if not url.startswith(SQLITE_PREFIX):
        return "DATABASE_URL must be a SQLite file."
    path = url[len(SQLITE_PREFIX):].split("?")[0]
    if not path or ":memory:" in path:
        return "DATABASE_URL must be a SQLite file."
    database = Path(path)
    if database.exists() and database.stat().st_size > 0:
        return "The database file already exists and is not empty."
    return None


# (days from today, start time, name, phone, service)
APPOINTMENTS = [
    (1, time(9, 0), "Anna Example", "+41790000101", "classic-cut"),
    (1, time(10, 0), "Ben Sample", "+41790000102", "classic-cut"),  # the fully booked 10:00
    (1, time(11, 0), "Clara Demo", "+41790000103", "cut-beard-combo"),
    (1, time(14, 0), "David Fictional", "+41790000104", "beard-sculpt"),
    (2, time(10, 0), "Erin Testcase", "+41790000105", "signature-fade"),
    (3, time(15, 0), "Finn Madeup", "+41790000106", "junior-cut"),
]
# (days from today, earliest start, latest start, name, phone, service)
WAITLIST = [
    (1, time(16, 0), time(17, 0), "Flora Placeholder", "+41790000107", "hot-towel-shave"),
]


def seed() -> None:
    # Imported here so that nothing connects to a database until the checks have passed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.database import Base, SessionLocal, engine
    from app.schema_check import require_current_schema
    from app.services.booking import book_slot_idempotent
    from app.services.catalog import SERVICES, staff_note
    from app.services.waitlist import join_waitlist
    from app.timezone import today

    Base.metadata.create_all(bind=engine)
    require_current_schema(engine)

    print("Seeded fictional demo data:")
    with SessionLocal() as db:
        for days, start, name, phone, service_id in APPOINTMENTS:
            service = SERVICES[service_id]
            result = book_slot_idempotent(
                db,
                name=name,
                phone=phone,
                appointment_date=today() + timedelta(days=days),
                appointment_time=start,
                service_id=service_id,
                duration_minutes=service["duration_minutes"],
                note=staff_note(service),
            )
            entry = result.entry
            print(f"  {entry.queue_date} {start:%H:%M}  {name}  {service['name']}")

        for days, earliest, latest, name, phone, service_id in WAITLIST:
            service = SERVICES[service_id]
            result = join_waitlist(
                db,
                name=name,
                phone=phone,
                service_id=service_id,
                duration_minutes=service["duration_minutes"],
                requested_date=today() + timedelta(days=days),
                window_start=earliest,
                window_end=latest,
                consent=True,
            )
            entry = result.entry
            print(f"  {entry.requested_date} {earliest:%H:%M}-{latest:%H:%M}  {name}  {service['name']} (waitlist)")


def main() -> int:
    reason = refusal()
    if reason is not None:
        print(f"Refusing to seed: {reason}", file=sys.stderr)
        return 2
    seed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
