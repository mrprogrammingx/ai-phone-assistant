"""Lists phone requests that staff must follow up by hand.

    python -m app.scripts.list_requests
    python -m app.scripts.list_requests --handled 12

Run locally only. Names and numbers appear on this terminal and are never logged.
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from app.database import Base, SessionLocal, engine
from app.schema_check import require_current_schema
from app.services.call_requests import delete_expired_requests, mark_handled, open_follow_ups
from app.services.catalog import SERVICES
from app.timezone import utc_to_local

BANNER = "MANUAL FOLLOW-UP: these callers do not have a confirmed appointment."


def _parse(arguments: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="list_requests", description="List or close phone follow-ups.")
    parser.add_argument("--handled", type=int, metavar="ID", help="mark this request as handled")
    try:
        return parser.parse_args(arguments)
    except SystemExit:
        return None


def _shown(value) -> str:
    return "-" if value is None else str(value)


def main(arguments: list[str] | None = None) -> int:
    options = _parse(sys.argv[1:] if arguments is None else arguments)
    if options is None:
        return 2

    Base.metadata.create_all(bind=engine)
    require_current_schema(engine)

    with SessionLocal() as db:
        delete_expired_requests(db)

        if options.handled is not None:
            if mark_handled(db, options.handled):
                print(f"Marked request {options.handled} as handled.")
                return 0
            print(f"No open follow-up has id {options.handled}.", file=sys.stderr)
            return 1

        print(BANNER)
        requests = open_follow_ups(db)
        if not requests:
            print("No open follow-ups.")
        for request in requests:
            service = SERVICES.get(request.service_id, {}).get("name")
            print(
                f"#{request.id} | {_shown(request.name)} | {_shown(request.phone)} | {_shown(service)} | "
                f"{_shown(request.requested_date)} | {_shown(request.requested_time)} | {request.reason}"
            )
            if request.suggested_times:
                as_of = utc_to_local(request.suggested_as_of)
                print(f"    Free as of {as_of:%Y-%m-%d %H:%M}, may be taken since: {request.suggested_times}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
