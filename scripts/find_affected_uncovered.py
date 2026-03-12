#!/usr/bin/env python3
"""Find guides affected by uncovered hours fix (vacation/sick) in 11/2025."""
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache

APP_UTILS_PATH = Path(__file__).resolve().parent.parent / "app_utils.py"
OLD_COND = "if (year, month) >= (2025, 12):"
NEW_COND = "if (year, month) >= (2025, 11):"


def calc(conn, pid, year, month, sc, mw, reports):
    """Calculate 100%/125%/150% hours for a guide."""
    import app_utils
    try:
        daily, _ = app_utils.get_daily_segments_data(
            conn, pid, year, month, sc, mw, preloaded_reports=reports
        )
        totals = app_utils.aggregate_daily_segments_to_monthly(conn, daily, pid, year, month, mw)
        c100 = round((totals.get("calc100", 0) or 0) / 60, 2)
        c125 = round((totals.get("calc125", 0) or 0) / 60, 2)
        c150 = round((totals.get("calc150", 0) or 0) / 60, 2)
        return c100, c125, c150
    except Exception as e:
        print(f"  Error pid={pid}: {e}")
        return None, None, None


REPORTS_SQL = """
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
"""


def write_results(affected, year, month, mw):
    """Write results to output file."""
    out = Path(__file__).parent / "affected_uncovered_result.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"=== Guides affected by uncovered hours fix (vacation/sick) {month:02d}/{year} ===\n")
        f.write("Old = day-of-week logic (is_saturday/is_friday) for uncovered hours\n")
        f.write("New = shift-type logic (tagbur + vacation/sick blocking)\n")
        f.write("Positive delta = old had MORE hours (overpaid)\n")
        f.write("Negative delta = old had FEWER hours (underpaid)\n")
        f.write(f"Minimum wage: {mw} ILS\n\n")

        if not affected:
            f.write("No affected guides found.\n")
        else:
            hdr = '{:<25} {:>8} {:>8} {:>7}  {:>8} {:>8} {:>7}  {:>8} {:>8} {:>7}  {:>10}'.format(
                'Name', '100%old', '100%new', 'd100', '125%old', '125%new', 'd125',
                '150%old', '150%new', 'd150', 'Amount')
            f.write(hdr + "\n")
            f.write("-" * len(hdr) + "\n")

            tot = 0.0
            for g in affected:
                line = '{:<25} {:>8.2f} {:>8.2f} {:>+7.2f}  {:>8.2f} {:>8.2f} {:>+7.2f}  {:>8.2f} {:>8.2f} {:>+7.2f}  {:>+10.2f}'.format(
                    g['name'],
                    g['c100_old'], g['c100_new'], g['diff100'],
                    g['c125_old'], g['c125_new'], g['diff125'],
                    g['c150_old'], g['c150_new'], g['diff150'],
                    g['amount'])
                f.write(line + "\n")
                tot += g['amount']

            f.write("-" * len(hdr) + "\n")
            total_line = '{:<25} {:>8} {:>8} {:>7}  {:>8} {:>8} {:>7}  {:>8} {:>8} {:>7}  {:>+10.2f}'.format(
                'TOTAL', '', '', '', '', '', '', '', '', '', tot)
            f.write(total_line + "\n")
            f.write(f"\nTotal affected: {len(affected)} guides\n")
            f.write(f"Total monetary impact: {tot:+.2f} ILS\n")
            f.write("(Positive = organization overpaid with old logic)\n")

    print(f"\nResults written to {out}")
    return out

def main() -> None:
    year, month = 2025, 11
    src = APP_UTILS_PATH.read_text(encoding="utf-8")

    if OLD_COND not in src:
        print("ERROR: condition not found in app_utils.py")
        sys.exit(1)

    try:
        with get_conn() as conn:
            sc = get_shabbat_times_cache(conn)
            row = conn.execute(
                "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
            ).fetchone()
            mw = float(row["hourly_rate"]) / 100 if row else 34.40

            date_from = f"{year}-{month:02d}-01"
            date_to = f"{year}-{month+1:02d}-01"

            guides = conn.execute(
                "SELECT DISTINCT tr.person_id, p.name FROM time_reports tr "
                "JOIN people p ON p.id = tr.person_id "
                "WHERE tr.date >= %s AND tr.date < %s ORDER BY p.name",
                (date_from, date_to)
            ).fetchall()

            print(f"Checking {len(guides)} guides for {month:02d}/{year}...")
            print(f"Minimum wage: {mw}")
            affected = []

            # Warm-up: reload app_utils twice to flush module cache
            # (Python 3.14 optimization: first reload after import may not change behavior)
            import app_utils
            importlib.reload(app_utils)
            importlib.reload(app_utils)

            for i, guide in enumerate(guides, 1):
                pid = guide["person_id"]
                name = guide["name"]

                reps = conn.execute(REPORTS_SQL, (pid, date_from, date_to)).fetchall()
                if not reps:
                    continue

                print(f"  [{i}/{len(guides)}] {name}...", end="", flush=True)

                # Old logic (current code, threshold at 2025,12)
                APP_UTILS_PATH.write_text(src, encoding="utf-8")
                importlib.reload(app_utils)
                o100, o125, o150 = calc(conn, pid, year, month, sc, mw, reps)

                # New logic (threshold changed to 2025,11)
                modified = src.replace(OLD_COND, NEW_COND)
                APP_UTILS_PATH.write_text(modified, encoding="utf-8")
                importlib.reload(app_utils)
                n100, n125, n150 = calc(conn, pid, year, month, sc, mw, reps)

                # Restore
                APP_UTILS_PATH.write_text(src, encoding="utf-8")
                importlib.reload(app_utils)

                if o100 is None or n100 is None:
                    print(" skip (error)")
                    continue

                d100, d125, d150 = o100 - n100, o125 - n125, o150 - n150

                if abs(d100) > 0.01 or abs(d125) > 0.01 or abs(d150) > 0.01:
                    amt = d100 * mw + d125 * mw * 1.25 + d150 * mw * 1.5
                    affected.append({
                        "name": name,
                        "c100_old": o100, "c100_new": n100, "diff100": d100,
                        "c125_old": o125, "c125_new": n125, "diff125": d125,
                        "c150_old": o150, "c150_new": n150, "diff150": d150,
                        "amount": amt,
                    })
                    print(f" AFFECTED (d100={d100:+.2f}, d125={d125:+.2f}, d150={d150:+.2f})")
                else:
                    print(" ok")

    finally:
        APP_UTILS_PATH.write_text(src, encoding="utf-8")
        print("\nSource file restored.")

    write_results(affected, year, month, mw)

    if affected:
        print(f"Total affected: {len(affected)} guides")
        for g in affected:
            nm = g["name"]
            d1 = g["diff100"]
            d2 = g["diff125"]
            d3 = g["diff150"]
            amt = g["amount"]
            print(f"  {nm}: d100={d1:+.2f}, d125={d2:+.2f}, d150={d3:+.2f}, amount={amt:+.2f} ILS")


if __name__ == "__main__":
    main()