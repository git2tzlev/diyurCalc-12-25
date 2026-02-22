"""
השוואת קבצי גשר: NEW (today) vs OLD (09/02)
מזהה הבדלים ובודק אם הם מוסברים ע"י דיווחים שנמחקו
"""
import sys
import os
import logging

# Suppress ALL logging
logging.disable(logging.CRITICAL)

PROJECT_ROOT = r"f:\DiyurClock\104\diyur003"
sys.path.insert(0, PROJECT_ROOT)

# Load .env before any imports
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from core.database import get_conn

# --- File paths ---
NEW_FILE = os.path.join(PROJECT_ROOT, "gesher_400_2026_01 (2).mrv")
OLD_FILE = os.path.join(PROJECT_ROOT, "gesher_400_2026_01 (4).mrv202602091316392026020913204720260209132204")

SYMBOL_NAMES = {
    "360": "hours@100%",
    "362": "shabbat@100%",
    "366": "hours@125%",
    "368": "hours@150%",
    "374": "shabbat@50%",
    "382": "hours@175%",
    "434": "hours@200%",
    "767": "TOTAL",
    "373": "standby",
    "370": "travel",
    "371": "bonus",
    "363": "global_pay",
    "319": "sick_leave",
    "243": "clothing",
    "299": "sick_accrual",
    "698": "vacation_accrual",
    "33":  "sick_days_used",
}


def parse_gesher_file(filepath: str) -> dict:
    """מפרסר קובץ גשר ומחזיר {employee_code: {symbol: (quantity, rate)}}"""
    data = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or len(line) < 30:
                continue
            # Header line check
            if line[0:3].strip().isdigit() and " " in line[3:6]:
                # Might be header like "400 26 01      0"
                try:
                    int(line[0:3].strip())
                    if line[4:6].strip().isdigit() and line[7:9].strip().isdigit():
                        continue
                except ValueError:
                    pass

            # Parse: XXXXXX SSS QQQQ.QQ RRRRRR.RR          201
            emp_code = line[0:6].strip()
            symbol = line[6:10].strip()
            qty_str = line[10:17].strip()
            rate_str = line[18:26].strip()

            if not emp_code or not symbol:
                continue

            try:
                qty = float(qty_str)
                rate = float(rate_str)
            except ValueError:
                continue

            if emp_code not in data:
                data[emp_code] = {}
            data[emp_code][symbol] = (qty, rate)

    return data


def get_db_info(conn, employee_codes: list) -> dict:
    """
    עבור כל קוד עובד, בודק דיווחים חסרים (gaps in IDs) עבור ינואר 2026
    מחזיר {meirav_code: {person_id, name, total_reports, missing_ids, missing_count}}
    """
    if not employee_codes:
        return {}

    cursor = conn.execute("""
        SELECT p.id, p.name, p.meirav_code
        FROM people p
        WHERE p.meirav_code IS NOT NULL AND p.meirav_code != ''
    """)
    rows = cursor.fetchall()

    # Build mapping: meirav_code -> (person_id, name)
    code_to_person = {}
    for r in rows:
        code_clean = ''.join(filter(str.isdigit, str(r['meirav_code'])))
        if code_clean:
            padded = code_clean.zfill(6)
            code_to_person[padded] = (r['id'], r['name'])

    results = {}
    for emp_code in employee_codes:
        if emp_code not in code_to_person:
            results[emp_code] = {
                "person_id": None,
                "name": "NOT FOUND IN DB",
                "total_reports": 0,
                "missing_ids": [],
                "missing_count": 0,
            }
            continue

        person_id, name = code_to_person[emp_code]

        # Get all report IDs for this person in Jan 2026
        cursor = conn.execute("""
            SELECT tr.id
            FROM time_reports tr
            WHERE tr.person_id = %s
              AND EXTRACT(YEAR FROM tr.date) = 2026
              AND EXTRACT(MONTH FROM tr.date) = 1
            ORDER BY tr.id
        """, (person_id,))
        report_rows = cursor.fetchall()
        report_ids = [r['id'] for r in report_rows]

        missing_ids = []
        if report_ids:
            min_id = min(report_ids)
            max_id = max(report_ids)
            all_ids_set = set(report_ids)

            # Check for gaps - get ALL IDs in the range across ALL employees
            # to see which ones belong to this person vs deleted
            cursor = conn.execute("""
                SELECT id FROM time_reports
                WHERE id BETWEEN %s AND %s
                ORDER BY id
            """, (min_id, max_id))
            existing_in_range = {r['id'] for r in cursor.fetchall()}

            # Missing = IDs in range that don't exist at all (deleted)
            for i in range(min_id, max_id + 1):
                if i not in existing_in_range:
                    missing_ids.append(i)

        results[emp_code] = {
            "person_id": person_id,
            "name": name,
            "total_reports": len(report_ids),
            "missing_ids": missing_ids,
            "missing_count": len(missing_ids),
        }

    return results


def main():
    print("=" * 100)
    print("GESHER FILE COMPARISON: NEW (today 19/02) vs OLD (09/02)")
    print("=" * 100)

    new_data = parse_gesher_file(NEW_FILE)
    old_data = parse_gesher_file(OLD_FILE)

    print(f"\nNEW file: {len(new_data)} employees")
    print(f"OLD file: {len(old_data)} employees")

    all_employees = sorted(set(list(new_data.keys()) + list(old_data.keys())))

    # --- Find differences ---
    only_in_new = []
    only_in_old = []
    diffs = {}  # emp_code -> list of (symbol, old_qty, old_rate, new_qty, new_rate)

    for emp in all_employees:
        if emp in new_data and emp not in old_data:
            only_in_new.append(emp)
            continue
        if emp in old_data and emp not in new_data:
            only_in_old.append(emp)
            continue

        # Both exist - compare
        old_symbols = old_data[emp]
        new_symbols = new_data[emp]
        all_syms = sorted(set(list(old_symbols.keys()) + list(new_symbols.keys())))

        for sym in all_syms:
            old_val = old_symbols.get(sym, (0.0, 0.0))
            new_val = new_symbols.get(sym, (0.0, 0.0))
            if abs(old_val[0] - new_val[0]) > 0.001 or abs(old_val[1] - new_val[1]) > 0.001:
                if emp not in diffs:
                    diffs[emp] = []
                diffs[emp].append((sym, old_val[0], old_val[1], new_val[0], new_val[1]))

    # Print employees only in one file
    if only_in_new:
        print(f"\n--- Employees ONLY in NEW (not in OLD): {len(only_in_new)} ---")
        for emp in only_in_new:
            total = new_data[emp].get("767", (0, 0))
            print(f"  {emp}: total hours={total[0]}, total pay={total[1]}")

    if only_in_old:
        print(f"\n--- Employees ONLY in OLD (not in NEW): {len(only_in_old)} ---")
        for emp in only_in_old:
            total = old_data[emp].get("767", (0, 0))
            print(f"  {emp}: total hours={total[0]}, total pay={total[1]}")

    # Employees with differences
    employees_with_diffs = sorted(diffs.keys())
    print(f"\n--- Employees with DIFFERENT values: {len(employees_with_diffs)} ---")

    if not employees_with_diffs:
        print("  No differences found!")
        return

    # Show all symbol differences
    print(f"\n{'='*100}")
    print(f"{'EMP':>8s} {'SYM':>5s} {'SYM NAME':<15s} {'OLD QTY':>10s} {'NEW QTY':>10s} {'QTY DIFF':>10s} {'OLD RATE':>10s} {'NEW RATE':>10s} {'RATE DIFF':>10s}")
    print(f"{'='*100}")

    for emp in employees_with_diffs:
        for sym, old_q, old_r, new_q, new_r in diffs[emp]:
            sym_name = SYMBOL_NAMES.get(sym, sym)
            q_diff = new_q - old_q
            r_diff = new_r - old_r
            print(f"{emp:>8s} {sym:>5s} {sym_name:<15s} {old_q:>10.2f} {new_q:>10.2f} {q_diff:>+10.2f} {old_r:>10.2f} {new_r:>10.2f} {r_diff:>+10.2f}")

    # Summary: symbol 767 (total line) differences
    print(f"\n{'='*100}")
    print("TOTAL LINE (767) DIFFERENCES:")
    print(f"{'='*100}")
    print(f"{'EMP':>8s} {'OLD HRS':>10s} {'NEW HRS':>10s} {'HRS DIFF':>10s} {'OLD PAY':>10s} {'NEW PAY':>10s} {'PAY DIFF':>10s}")
    print(f"{'-'*78}")

    total_hour_diff = 0.0
    total_pay_diff = 0.0
    emp_767_diffs = {}

    for emp in employees_with_diffs:
        for sym, old_q, old_r, new_q, new_r in diffs[emp]:
            if sym == "767":
                q_diff = new_q - old_q
                r_diff = new_r - old_r
                total_hour_diff += q_diff
                total_pay_diff += r_diff
                emp_767_diffs[emp] = (old_q, new_q, q_diff, old_r, new_r, r_diff)
                print(f"{emp:>8s} {old_q:>10.2f} {new_q:>10.2f} {q_diff:>+10.2f} {old_r:>10.2f} {new_r:>10.2f} {r_diff:>+10.2f}")

    print(f"{'-'*78}")
    print(f"{'TOTAL':>8s} {'':>10s} {'':>10s} {total_hour_diff:>+10.2f} {'':>10s} {'':>10s} {total_pay_diff:>+10.2f}")

    # --- Database check for missing report IDs ---
    print(f"\n{'='*100}")
    print("DATABASE CHECK: Missing report IDs (gaps) for employees with differences")
    print(f"{'='*100}")

    conn = get_conn()
    try:
        db_info = get_db_info(conn, employees_with_diffs)

        print(f"\n{'EMP':>8s} {'NAME':<25s} {'REPORTS':>8s} {'MISSING':>8s} {'HRS DIFF':>10s} {'PAY DIFF':>10s} {'TYPE':>8s}")
        print(f"{'-'*100}")

        type_a = []  # Explained by deleted reports
        type_b = []  # NOT explained

        for emp in employees_with_diffs:
            info = db_info.get(emp, {})
            name = info.get("name", "???")[:25]
            total_rep = info.get("total_reports", 0)
            missing_count = info.get("missing_count", 0)

            hrs_diff = 0.0
            pay_diff = 0.0
            if emp in emp_767_diffs:
                _, _, hrs_diff, _, _, pay_diff = emp_767_diffs[emp]

            if missing_count > 0:
                category = "TYPE_A"
                type_a.append(emp)
            else:
                category = "TYPE_B"
                type_b.append(emp)

            print(f"{emp:>8s} {name:<25s} {total_rep:>8d} {missing_count:>8d} {hrs_diff:>+10.2f} {pay_diff:>+10.2f} {category:>8s}")

        # --- Final Summary ---
        print(f"\n{'='*100}")
        print("CLASSIFICATION SUMMARY")
        print(f"{'='*100}")
        print(f"\nTYPE A - Difference likely EXPLAINED by deleted reports ({len(type_a)} employees):")
        print("  (These employees have gaps in their time_report IDs = deleted shifts)")
        for emp in type_a:
            info = db_info[emp]
            hrs_d = emp_767_diffs.get(emp, (0,0,0,0,0,0))[2]
            pay_d = emp_767_diffs.get(emp, (0,0,0,0,0,0))[5]
            print(f"  {emp} ({info['name'][:30]}) - {info['missing_count']} missing IDs, hrs_diff={hrs_d:+.2f}, pay_diff={pay_d:+.2f}")

        print(f"\nTYPE B - Difference NOT explained by deleted reports ({len(type_b)} employees):")
        print("  (These employees have NO gaps => difference is from CODE CHANGE)")
        for emp in type_b:
            info = db_info[emp]
            hrs_d = emp_767_diffs.get(emp, (0,0,0,0,0,0))[2]
            pay_d = emp_767_diffs.get(emp, (0,0,0,0,0,0))[5]
            print(f"  {emp} ({info['name'][:30]}) - 0 missing IDs, hrs_diff={hrs_d:+.2f}, pay_diff={pay_d:+.2f}")

        # --- Detailed diff per employee ---
        print(f"\n{'='*100}")
        print("DETAILED DIFFERENCES PER EMPLOYEE")
        print(f"{'='*100}")

        for emp in employees_with_diffs:
            info = db_info.get(emp, {})
            name = info.get("name", "???")
            category = "TYPE_A" if emp in type_a else "TYPE_B"
            print(f"\n--- {emp} ({name}) [{category}] ---")
            if info.get("missing_count", 0) > 0:
                print(f"    Missing IDs: {info['missing_count']} gaps in time_reports ID sequence")
            for sym, old_q, old_r, new_q, new_r in diffs[emp]:
                sym_name = SYMBOL_NAMES.get(sym, sym)
                q_diff = new_q - old_q
                r_diff = new_r - old_r
                print(f"    {sym:>3s} ({sym_name:<15s}): qty {old_q:>8.2f} -> {new_q:>8.2f} ({q_diff:>+8.2f}), rate {old_r:>10.2f} -> {new_r:>10.2f} ({r_diff:>+10.2f})")

        # Grand totals
        print(f"\n{'='*100}")
        print("GRAND TOTALS")
        print(f"{'='*100}")
        print(f"Total employees in files: NEW={len(new_data)}, OLD={len(old_data)}")
        print(f"Employees with differences: {len(employees_with_diffs)}")
        print(f"  - TYPE A (deleted reports): {len(type_a)}")
        print(f"  - TYPE B (code change):     {len(type_b)}")
        print(f"Total hours difference (767): {total_hour_diff:+.2f}")
        print(f"Total pay difference (767):   {total_pay_diff:+.2f}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
