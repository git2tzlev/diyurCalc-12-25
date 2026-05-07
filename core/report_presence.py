"""Shared queries for detecting guides that have reportable monthly data."""
from __future__ import annotations

from datetime import date
from typing import Optional


def get_report_presence_counts(
    conn,
    start_date: date,
    end_date: date,
    housing_array_id: Optional[int] = None,
) -> tuple[dict[int, int], set[int]]:
    """Return shift counts and payment-component-only guide ids for a period."""
    counts: dict[int, int] = {}
    has_payment_components: set[int] = set()

    if housing_array_id is not None:
        for row in conn.execute(
            """
            SELECT tr.person_id, COUNT(*) AS cnt
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
            GROUP BY tr.person_id
            """,
            (start_date, end_date, housing_array_id),
        ):
            counts[row["person_id"]] = row["cnt"]

        for row in conn.execute(
            """
            SELECT DISTINCT pc.person_id
            FROM payment_components pc
            JOIN apartments ap ON ap.id = pc.apartment_id
            WHERE pc.date >= %s AND pc.date < %s
              AND ap.housing_array_id = %s
            """,
            (start_date, end_date, housing_array_id),
        ):
            has_payment_components.add(row["person_id"])
    else:
        for row in conn.execute(
            """
            SELECT person_id, COUNT(*) AS cnt
            FROM time_reports
            WHERE date >= %s AND date < %s
            GROUP BY person_id
            """,
            (start_date, end_date),
        ):
            counts[row["person_id"]] = row["cnt"]

        for row in conn.execute(
            """
            SELECT DISTINCT person_id
            FROM payment_components
            WHERE date >= %s AND date < %s
            """,
            (start_date, end_date),
        ):
            has_payment_components.add(row["person_id"])

    return counts, has_payment_components
