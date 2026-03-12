#!/usr/bin/env python3
"""
Debug script v3: Investigate calc_variable (363) vs calc100 (360) discrepancies for 11/2025.

FINDINGS SUMMARY:
================

There are TWO separate issues causing the 6 discrepancies:

ISSUE 1 - Comparison script bug (affects guides 7331, 5924):
  File: scripts/compare_pdf_vs_system.py, lines 203-219
  The preloaded_reports SQL query is MISSING: st.is_special_hourly AS shift_is_special_hourly
  This causes get_daily_segments_data to treat ALL shifts as is_special_hourly=False.
  Result: Shift 148 (medical escort, is_special_hourly=True, rate=minimum_wage) is
  classified as REGULAR instead of VARIABLE, moving hours from calc_variable to calc100.
  FIX: Add "st.is_special_hourly AS shift_is_special_hourly" to the query.

ISSUE 2 - New variable rate logic (affects guides 1734, 7293, 4515, 10717):
  These guides have shifts 138 (Work Hour, rate=40 NIS from housing_rates) or
  149 (Professional Support, is_special_hourly=True, rate=40 NIS).
  The current system correctly classifies these as variable rate (code 363).
  The Merav payslip did NOT have code 363 for these guides, suggesting Merav
  classifies them differently (as regular rate).

KEY COMMITS IN VARIABLE RATE LOGIC EVOLUTION:
  1. 90c21ba (2026-01-15): First introduced is_variable_rate in aggregate function
     Formula: is_variable_rate = abs(effective_rate - minimum_wage) > 0.01
  2. c4e5615 (2026-01-22): Added is_special_hourly flag to the formula
     Formula: is_variable_rate = is_special_hourly or abs(effective_rate - minimum_wage) > 0.01
  3. 3e01b2f (2026-03-06): Added hourly_wage_supplement to the formula
     Formula: is_variable_rate = is_special_hourly or abs(effective_rate - minimum_wage - supplement) > 0.01
  4. 89d81a4 (2026-02-09): Changed uncovered minutes to use WORK_HOUR_SHIFT_ID (138)
     instead of the report's original shift_type_id. Shift 138 has weekday_married_rate=40 NIS,
     making uncovered minutes for married guides classified as variable rate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.history import get_minimum_wage_for_month
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly
from services.gesher_exporter import load_export_config_from_db, calculate_value

AFFECTED_GUIDES = [7331, 5924, 1734, 7293, 4515, 10717]


def main() -> None:
    out_lines = []

    def log(msg: str = "") -> None:
        print(msg)
        out_lines.append(msg)

    log("=" * 80)
    log("VARIABLE RATE DEBUG: calc_variable (363) vs calc100 (360) - 11/2025")
    log("=" * 80)
    log()

    with get_conn() as conn:
        min_wage = get_minimum_wage_for_month(conn, 2025, 11)
        shabbat_cache = get_shabbat_times_cache(conn)
        export_config = load_export_config_from_db(conn)
        log(f"Minimum wage for 11/2025: {min_wage} NIS/hour")
        log()

        for emp_num in AFFECTED_GUIDES:
            person = conn.execute(
                "SELECT id, name, meirav_code FROM people WHERE meirav_code = %s",
                (str(emp_num),)
            ).fetchone()
            if not person:
                log(f"ERROR: No person found with meirav_code={emp_num}")
                continue

            pid = person["id"]
            pname = person["name"]
            log("-" * 70)
            log(f"Guide: {pname} (meirav_code={emp_num}, person_id={pid})")
            log("-" * 70)

            # CORRECT calculation (full internal query)
            daily1, _ = get_daily_segments_data(conn, pid, 2025, 11, shabbat_cache, min_wage)
            totals1 = aggregate_daily_segments_to_monthly(conn, daily1, pid, 2025, 11, min_wage)

            correct_360, _ = calculate_value(totals1, "calc100", "hours_100", min_wage)
            correct_363_qty, correct_363_rate = calculate_value(totals1, "calc_variable", "variable_rate_payment", min_wage)

            # BUGGY calculation (comparison script query missing is_special_hourly)
            reports_buggy = conn.execute("""
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
                WHERE tr.person_id = %s AND tr.date >= %s AND tr.date < %s
                ORDER BY tr.date, tr.start_time
            """, (pid, "2025-11-01", "2025-12-01")).fetchall()

            daily2, _ = get_daily_segments_data(
                conn, pid, 2025, 11, shabbat_cache, min_wage,
                preloaded_reports=reports_buggy
            )
            totals2 = aggregate_daily_segments_to_monthly(conn, daily2, pid, 2025, 11, min_wage)

            buggy_360, _ = calculate_value(totals2, "calc100", "hours_100", min_wage)
            buggy_363_qty, buggy_363_rate = calculate_value(totals2, "calc_variable", "variable_rate_payment", min_wage)

            log(f"  CORRECT: 360={correct_360}h, 363 qty={correct_363_qty} rate={correct_363_rate}")
            log(f"  BUGGY:   360={buggy_360}h, 363 qty={buggy_363_qty} rate={buggy_363_rate}")
            if abs(correct_360 - buggy_360) > 0.01 or abs(correct_363_qty - buggy_363_qty) > 0.01:
                log(f"  ** MISMATCH due to missing is_special_hourly in compare script! **")
            else:
                log(f"  (No difference from compare script bug)")
            log()

    out_path = Path(__file__).parent / "debug_variable_rate_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    main()
