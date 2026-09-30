"""Persistent request budgeting for the news collection pipeline."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


WINDOW_SECONDS = 24 * 60 * 60


def reserve_news_request(
    state_path: Path,
    *,
    request_limit: int,
    now: datetime | None = None,
) -> bool:
    """Reserve one request within a rolling 24-hour budget.

    Return False when the budget is exhausted.
    Reservations are not refunded after failed or interrupted requests.
    Use the same state file across runs sharing a request budget.
    """
    if type(request_limit) is not int or request_limit < 1:
        raise ValueError("request_limit must be a positive integer.")

    if now is not None and now.utcoffset() is None:
        raise ValueError("now must include timezone information.")

    state_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(
        sqlite3.connect(
            state_path,
            timeout=30,
            isolation_level=None,
        )
    ) as connection:
        # Lock before checking and reserving to avoid concurrent overspending.
        connection.execute("BEGIN IMMEDIATE")

        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS news_request_reservations (
                    id INTEGER PRIMARY KEY,
                    reserved_at REAL NOT NULL
                )
                """
            )

            timestamp = (
                now if now is not None else datetime.now(timezone.utc)
            ).timestamp()
            cutoff = timestamp - WINDOW_SECONDS

            used = connection.execute(
                """
                SELECT COUNT(*)
                FROM news_request_reservations
                WHERE reserved_at > ?
                """,
                (cutoff,),
            ).fetchone()[0]

            if used >= request_limit:
                connection.commit()
                return False

            connection.execute(
                """
                INSERT INTO news_request_reservations (reserved_at)
                VALUES (?)
                """,
                (timestamp,),
            )
            connection.commit()
            return True

        except Exception:
            connection.rollback()
            raise
