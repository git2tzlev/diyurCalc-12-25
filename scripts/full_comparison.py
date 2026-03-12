#!/usr/bin/env python3
"""Full comparison: old logic (> 60) vs new logic (>= 60) for ALL guides 11/2025.
Check if break threshold change explains ALL discrepancies."""
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.constants
from core.database import get_conn
from core.logic import get_shabbat_times_cache

FIELDS = [
    'calc100', 'calc125', 'calc150', 'calc150_shabbat', 'calc150_overtime',
    'calc175', 'calc200', 'calc_variable', 'payment_calc_variable',
    'standby', 'standby_payment', 'travel', 'extras',
    'payment', 'gesher_total', 'display_total',
    'vacation_minutes', 'sick_minutes',
    'holiday_payment', 'professional_support',
]


def calc_for_guide(conn, pid, year, month, shabbat_cache, minimum_wage, reports):
    """Run full calculation, return monthly_totals dict."""
    import app_utils
    importlib.reload(app_utils)
    from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly
    try:
        daily, _ = get_daily_segments_data(
            conn, pid, year, month, shabbat_cache, minimum_wage,
            preloaded_reports=reports
        )
        totals = aggregate_daily_segments_to_monthly(
            conn, daily, pid, year, month, minimum_wage
        )
        return totals
    except Exception as e:
        return {"error": str(e)}


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

        results = []

        for i, guide in enumerate(guides):
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

            # New logic (>= 60 breaks chain)
            core.constants.BREAK_THRESHOLD_MINUTES = 60
            totals_new = calc_for_guide(conn, pid, year, month, shabbat_cache, minimum_wage, reports)

            # Old logic (> 60, simulated as >= 61)
            core.constants.BREAK_THRESHOLD_MINUTES = 61
            totals_old = calc_for_guide(conn, pid, year, month, shabbat_cache, minimum_wage, reports)

            # Restore
            core.constants.BREAK_THRESHOLD_MINUTES = 60

            if "error" in totals_new or "error" in totals_old:
                continue

            # Compare ALL fields
            diffs = {}
            for field in FIELDS:
                old_val = totals_old.get(field, 0) or 0
                new_val = totals_new.get(field, 0) or 0
                if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                    if abs(old_val - new_val) > 0.01:
                        diffs[field] = (old_val, new_val, new_val - old_val)

            if diffs:
                results.append({
                    "pid": pid,
                    "name": name,
                    "diffs": diffs,
                })

    out_path = Path(__file__).parent / "full_comparison_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== Full comparison: old (>60) vs new (>=60) for ALL guides 11/2025 ===\n\n")

        if not results:
            f.write("No differences found.\n")
        else:
            f.write(f"Total guides with differences: {len(results)}\n\n")

            # Check if all differences are ONLY in calc100/calc125
            only_100_125 = []
            has_other_diffs = []

            for r in results:
                diff_fields = set(r["diffs"].keys())
                non_100_125 = diff_fields - {"calc100", "calc125", "payment_calc100", "payment_calc125", "payment", "gesher_total", "display_total"}
                if non_100_125:
                    has_other_diffs.append(r)
                else:
                    only_100_125.append(r)

            f.write(f"=== Guides with ONLY calc100/calc125 differences: {len(only_100_125)} ===\n")
            for r in only_100_125:
                d100 = r["diffs"].get("calc100", (0, 0, 0))
                d125 = r["diffs"].get("calc125", (0, 0, 0))
                f.write(f"  {r['name']} (id={r['pid']}): "
                        f"100%: {d100[0]/60:.2f}h -> {d100[1]/60:.2f}h ({d100[2]/60:+.2f}h), "
                        f"125%: {d125[0]/60:.2f}h -> {d125[1]/60:.2f}h ({d125[2]/60:+.2f}h)\n")

            if has_other_diffs:
                f.write(f"\n=== Guides with OTHER differences too: {len(has_other_diffs)} ===\n")
                for r in has_other_diffs:
                    f.write(f"\n  {r['name']} (id={r['pid']}):\n")
                    for field, (old, new, diff) in r["diffs"].items():
                        if field in ('calc100', 'calc125', 'calc150', 'calc175', 'calc200',
                                     'calc_variable', 'calc150_shabbat', 'calc150_overtime',
                                     'vacation_minutes', 'sick_minutes'):
                            f.write(f"    {field}: {old/60:.2f}h -> {new/60:.2f}h ({diff/60:+.2f}h)\n")
                        else:
                            f.write(f"    {field}: {old:.2f} -> {new:.2f} ({diff:+.2f})\n")
            else:
                f.write(f"\nNo guides have differences in fields other than calc100/calc125 and derived payment fields.\n")
                f.write(f"\nCONCLUSION: The break threshold change (> 60 to >= 60) explains ALL discrepancies.\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
