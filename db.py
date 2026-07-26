"""SQLite storage for poll diff state."""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "d2bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messenger_state (
    membership_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create d2bot.db (and messenger_state) if missing."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def get_messenger_state(membership_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT state_json FROM messenger_state WHERE membership_id = ?",
            (membership_id,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["state_json"])


def set_messenger_state(membership_id: str, state: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO messenger_state (membership_id, state_json)
            VALUES (?, ?)
            ON CONFLICT(membership_id) DO UPDATE SET state_json = excluded.state_json
            """,
            (membership_id, json.dumps(state)),
        )
        conn.commit()
