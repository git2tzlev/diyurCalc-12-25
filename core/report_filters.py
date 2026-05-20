"""Shared filters for time_reports queries."""
from __future__ import annotations

from typing import Optional

from core.constants import (
    ASD_COMPLETION_EXCLUSION_MONTH,
    ASD_COMPLETION_EXCLUSION_YEAR,
    ASD_HOUSING_ARRAY_ID,
    COMPLETION_APARTMENT_IDS,
)


def completion_exclusion_sql_for_reports(
    year: int,
    month: int,
    housing_array_id: Optional[int],
    report_alias: str = "tr",
) -> tuple[str, tuple]:
    """SQL filter for the one-time ASD completion reports exclusion."""
    if (
        year != ASD_COMPLETION_EXCLUSION_YEAR
        or month != ASD_COMPLETION_EXCLUSION_MONTH
    ):
        return "", ()

    completion_ids = sorted(COMPLETION_APARTMENT_IDS)
    if housing_array_id is not None:
        if housing_array_id == ASD_HOUSING_ARRAY_ID:
            return f"AND {report_alias}.apartment_id <> ALL(%s)", (completion_ids,)
        return "", ()

    return (
        f"""
        AND NOT EXISTS (
            SELECT 1
            FROM apartments ap_exclude
            WHERE ap_exclude.id = {report_alias}.apartment_id
              AND ap_exclude.housing_array_id = %s
              AND {report_alias}.apartment_id = ANY(%s)
        )
        """,
        (ASD_HOUSING_ARRAY_ID, completion_ids),
    )
