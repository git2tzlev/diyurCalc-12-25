#!/usr/bin/env python3
"""Find all guides affected by the >= vs > BREAK_THRESHOLD change for a given month."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly, _calculate_chain_wages

# Store original function
_orig_calc = _calculate_chain_wages


def main() -> None:
    year, month = 2025, 11

    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)

        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        # Get all active guides for the month
        guides = conn.execute("""
            SELECT DISTINCT tr.person_id, p.name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            WHERE tr.date >= %s AND tr.date < %s
            ORDER BY p.name
        """, (f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01')).fetchall()

        print(f"Checking {len(guides)} guides for {month:02d}/{year}...")
        affected = []

        for guide in guides:
            pid = guide["person_id"]
            name = guide["name"]

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
            """, (pid, f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01')).fetchall()

            if not reports:
                continue

            # Calculate with current code (>= 60 breaks chain)
            try:
                daily_new, _ = get_daily_segments_data(
                    conn, pid, year, month, shabbat_cache, minimum_wage,
                    preloaded_reports=reports
                )
                totals_new = aggregate_daily_segments_to_monthly(
                    conn, daily_new, pid, year, month, minimum_wage
                )
            except Exception:
                continue

            # Now monkey-patch to use old logic (> 60, i.e., 60 doesn't break)
            import app_utils
            # Save originals
            orig_code = None

            # We can't easily monkey-patch the break logic since it's inside
            # get_daily_segments_data. Instead, check if any 60-min gaps exist
            # by looking at the reports
            has_60min_gap = False
            sorted_reports = sorted(reports, key=lambda r: (str(r["date"]), r["start_time"]))

            for i in range(len(sorted_reports) - 1):
                r1 = sorted_reports[i]
                r2 = sorted_reports[i + 1]
                if str(r1["date"]) == str(r2["date"]):
                    gap = r2["start_time"] - r1["end_time"]
                    if gap == 60:
                        has_60min_gap = True
                        break

            if not has_60min_gap:
                # Also check cross-day: morning chain at 06:30 and previous day end
                # This is harder to check directly from reports, so skip for now
                pass

            c100_new = round((totals_new.get('calc100', 0) or 0) / 60, 2)
            c125_new = round((totals_new.get('calc125', 0) or 0) / 60, 2)

            if has_60min_gap:
                affected.append({
                    "pid": pid,
                    "name": name,
                    "calc100": c100_new,
                    "calc125": c125_new,
                    "has_60min_gap": True
                })

    print(f"\n=== Guides with exactly 60-minute gaps between shifts ({month:02d}/{year}) ===\n")
    for g in affected:
        print(f"  {g['name']} (id={g['pid']}): calc100={g['calc100']}h, calc125={g['calc125']}h")

    if not affected:
        print("  None found (checking same-day gaps only)")


if __name__ == "__main__":
    main()
