#!/usr/bin/env python3
"""Check daily_segments structure."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def main():
    year, month = 2025, 11
    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)
        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        person_id = 211  # Kramer

        reports = conn.execute("""
            SELECT tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                   tr.apartment_id, st.name AS shift_name,
                   ap.housing_array_id, at.hourly_wage_supplement,
                   at.name AS apartment_type_name,
                   ap.apartment_type_id,
                   p.is_married, p.name as person_name,
                   ap.city AS apartment_city
            FROM time_reports tr
            LEFT JOIN shift_types st ON st.id = tr.shift_type_id
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
            LEFT JOIN people p ON p.id = tr.person_id
            WHERE tr.person_id = %s
              AND tr.date >= %s AND tr.date < %s
            ORDER BY tr.date, tr.start_time
        """, (person_id, f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01')).fetchall()

        daily_segments, _ = get_daily_segments_data(
            conn, person_id, year, month,
            shabbat_cache, minimum_wage,
            preloaded_reports=reports
        )

        # Print first day with standby
        for day in daily_segments:
            if day.get("standby_payment", 0):
                print("=== Day with standby ===")
                for k, v in day.items():
                    if k != "events":
                        if isinstance(v, (int, float, str, bool, type(None))):
                            print(f"  {k}: {v}")
                        else:
                            print(f"  {k}: (type={type(v).__name__})")
                    else:
                        print(f"  events: {len(v)} items")
                        for i, e in enumerate(v):
                            print(f"    [{i}] {e}")
                print()
                break  # just first


if __name__ == "__main__":
    main()
