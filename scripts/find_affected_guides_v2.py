#!/usr/bin/env python3
"""Find all guides affected by the >= vs > BREAK_THRESHOLD change.
Compares calculation with BREAK_THRESHOLD=60 (new) vs 61 (simulates old > 60 behavior)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.constants
from core.database import get_conn
from core.logic import get_shabbat_times_cache

ORIG_THRESHOLD = core.constants.BREAK_THRESHOLD_MINUTES


def calc_for_guide(conn, pid, year, month, shabbat_cache, minimum_wage, reports):
    """Run calculation and return (calc100, calc125) in hours."""
    from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly
    try:
        daily, _ = get_daily_segments_data(
            conn, pid, year, month, shabbat_cache, minimum_wage,
            preloaded_reports=reports
        )
        totals = aggregate_daily_segments_to_monthly(
            conn, daily, pid, year, month, minimum_wage
        )
        c100 = round((totals.get('calc100', 0) or 0) / 60, 2)
        c125 = round((totals.get('calc125', 0) or 0) / 60, 2)
        c150 = round((totals.get('calc150', 0) or 0) / 60, 2)
        c175 = round((totals.get('calc175', 0) or 0) / 60, 2)
        c200 = round((totals.get('calc200', 0) or 0) / 60, 2)
        return c100, c125, c150, c175, c200
    except Exception as e:
        return None, None, None, None, None


def main() -> None:
    year, month = 2025, 11

    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)

        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        guides = conn.execute("""
            SELECT DISTINCT tr.person_id, p.name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            WHERE tr.date >= %s AND tr.date < %s
            ORDER BY p.name
        """, (f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01')).fetchall()

        print(f"Checking {len(guides)} guides for {month:02d}/{year}...\n")
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

            # Current calculation (>= 60 breaks)
            core.constants.BREAK_THRESHOLD_MINUTES = 60
            # Need to reload app_utils to pick up the constant change
            import importlib
            import app_utils
            importlib.reload(app_utils)
            c100_new, c125_new, c150_new, c175_new, c200_new = calc_for_guide(
                conn, pid, year, month, shabbat_cache, minimum_wage, reports
            )

            # Old calculation (> 60, simulated as >= 61)
            core.constants.BREAK_THRESHOLD_MINUTES = 61
            importlib.reload(app_utils)
            c100_old, c125_old, c150_old, c175_old, c200_old = calc_for_guide(
                conn, pid, year, month, shabbat_cache, minimum_wage, reports
            )

            # Restore
            core.constants.BREAK_THRESHOLD_MINUTES = ORIG_THRESHOLD

            if c100_new is None or c100_old is None:
                continue

            if abs(c100_new - c100_old) > 0.01 or abs(c125_new - c125_old) > 0.01:
                diff100 = c100_new - c100_old
                diff125 = c125_new - c125_old
                affected.append({
                    "pid": pid,
                    "name": name,
                    "c100_old": c100_old,
                    "c100_new": c100_new,
                    "c125_old": c125_old,
                    "c125_new": c125_new,
                    "diff100": diff100,
                    "diff125": diff125,
                })

    out_path = Path(__file__).parent / "affected_guides_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== Guides affected by break threshold change ({month:02d}/{year}) ===\n")
        f.write(f"Change: gap > 60 → gap >= 60 (commit dc511af, 2026-02-22)\n\n")

        if not affected:
            f.write("No affected guides found.\n")
        else:
            f.write(f"{'שם':<25} {'100% ישן':>10} {'100% חדש':>10} {'Δ100%':>8} "
                    f"{'125% ישן':>10} {'125% חדש':>10} {'Δ125%':>8}\n")
            f.write("-" * 85 + "\n")
            for g in affected:
                f.write(f"{g['name']:<25} {g['c100_old']:>10.2f} {g['c100_new']:>10.2f} "
                        f"{g['diff100']:>+8.2f} {g['c125_old']:>10.2f} {g['c125_new']:>10.2f} "
                        f"{g['diff125']:>+8.2f}\n")

        f.write(f"\nTotal affected: {len(affected)} guides\n")

    print(f"Results written to {out_path}")
    for g in affected:
        print(f"  {g['name']}: Δ100%={g['diff100']:+.2f}h, Δ125%={g['diff125']:+.2f}h")


if __name__ == "__main__":
    main()
