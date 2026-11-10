"""Refuses to start against a database that lacks required columns.

`create_all` creates missing tables but never adds columns to existing ones, so an
older database would otherwise fail later, on the first booking.
"""
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

REQUIRED_COLUMNS = ("service_id", "duration_minutes")

MIGRATION_INSTRUCTIONS = """Back up the database first, then run these once.

SQLite:
    ALTER TABLE queue_entries ADD COLUMN service_id VARCHAR;
    ALTER TABLE queue_entries ADD COLUMN duration_minutes INTEGER;

Postgres:
    ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS service_id VARCHAR;
    ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;
"""

# Column name: (SQLite type, Postgres type).
CALL_REQUEST_COLUMNS = {
    "suggested_times": ("VARCHAR", "VARCHAR"),
    "suggested_as_of": ("DATETIME", "TIMESTAMP"),
}


class DatabaseSchemaOutOfDate(RuntimeError):
    pass


def _call_request_instructions(missing: list[str]) -> str:
    sqlite = "\n".join(
        f"    ALTER TABLE call_requests ADD COLUMN {column} {CALL_REQUEST_COLUMNS[column][0]};"
        for column in missing
    )
    postgres = "\n".join(
        f"    ALTER TABLE call_requests ADD COLUMN IF NOT EXISTS {column} {CALL_REQUEST_COLUMNS[column][1]};"
        for column in missing
    )
    return f"Back up the database first, then run these once.\n\nSQLite:\n{sqlite}\n\nPostgres:\n{postgres}\n"


def require_current_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    existing = {column["name"] for column in inspector.get_columns("queue_entries")}
    missing = [column for column in REQUIRED_COLUMNS if column not in existing]
    if missing:
        raise DatabaseSchemaOutOfDate(
            f"queue_entries is missing column(s): {', '.join(missing)}.\n\n{MIGRATION_INSTRUCTIONS}"
        )

    # create_all makes this table if it is absent, so only an older copy can lack columns.
    if inspector.has_table("call_requests"):
        existing = {column["name"] for column in inspector.get_columns("call_requests")}
        missing = [column for column in CALL_REQUEST_COLUMNS if column not in existing]
        if missing:
            raise DatabaseSchemaOutOfDate(
                f"call_requests is missing column(s): {', '.join(missing)}.\n\n{_call_request_instructions(missing)}"
            )
