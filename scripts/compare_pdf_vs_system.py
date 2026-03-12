#!/usr/bin/env python3
"""השוואת נתוני תלושים (PDF) מול חישוב המערכת הנוכחי ל-11/2025."""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly
from services.gesher_exporter import load_export_config_from_db, calculate_value

# קודי שעות שנרצה להשוות (qty = שעות)
HOUR_CODES = {
    '360': 'calc100',
    '362': 'shabbat_100',
    '366': 'calc125',
    '368': 'calc150',
    '374': 'shabbat_50',
    '376': 'vacation',
    '382': 'calc175',
    '434': 'calc200',
    '319': 'sick',
}
# calc_variable (363) - qty=1 means exists, rate=amount
# standby (373) - qty=1, rate=total payment
# travel (370) - qty=0, rate=amount (money)
Y_MAX = 420  # סוף אזור התשלומים (מורחב)


def extract_page_data(page) -> dict:
    """חילוץ נתונים מעמוד תלוש."""
    blocks = page.get_text('dict')
    all_lines = []

    for block in blocks['blocks']:
        if 'lines' not in block:
            continue
        for line in block['lines']:
            text = ''
            for span in line['spans']:
                text += span['text']
            if text.strip():
                bbox = line['bbox']
                all_lines.append((bbox[0], bbox[1], text.strip()))

    # מספר עובד
    employee_number = None
    for x, y, text in all_lines:
        if y < 25 and x > 540 and text.isdigit() and len(text) >= 4:
            employee_number = int(text)
            break

    # קודי תשלום - עמודה x~410-425, y בין 135-Y_MAX
    code_lines = {}
    for x, y, text in all_lines:
        if 410 < x < 425 and 135 < y < Y_MAX:
            if text.replace('-', '').isdigit() and text != '---':
                code_lines[round(y, 0)] = text

    # חיפוש code 360 בתוך טקסט תיאור (לפעמים חלק מהתיאור)
    for x, y, text in all_lines:
        if 280 < x < 345 and 135 < y < 170:
            m = re.search(r'360\b', text)
            if m:
                yr = round(y, 0)
                if yr not in code_lines:
                    code_lines[yr] = '360'

    # כמויות (qty) - x~218-245
    qty_lines = {}
    for x, y, text in all_lines:
        if 218 < x < 245 and 135 < y < Y_MAX:
            try:
                clean = text.replace('v', '').replace('\u05d5', '').strip()
                if clean:
                    qty_lines[round(y, 0)] = float(clean)
            except ValueError:
                pass

    # תעריפים (rate) - x~165-200
    rate_lines = {}
    for x, y, text in all_lines:
        if 160 < x < 205 and 135 < y < Y_MAX:
            try:
                clean = text.replace('v', '').replace('\u05d5', '').strip()
                if clean:
                    rate_lines[round(y, 0)] = float(clean)
            except ValueError:
                pass

    # סכומים (amount) - x~120-150
    amount_lines = {}
    for x, y, text in all_lines:
        if 115 < x < 155 and 135 < y < Y_MAX:
            try:
                amount_lines[round(y, 0)] = float(text)
            except ValueError:
                pass

    # חיבור
    payment_data = {}
    for y_pos, code in code_lines.items():
        qty = None
        rate = None
        amount = None
        for dy in range(-3, 4):
            yk = y_pos + dy
            if yk in qty_lines and qty is None:
                qty = qty_lines[yk]
            if yk in rate_lines and rate is None:
                rate = rate_lines[yk]
            if yk in amount_lines and amount is None:
                amount = amount_lines[yk]

        payment_data[code] = {
            'qty': qty or 0,
            'rate': rate or 0,
            'amount': amount or 0,
        }

    return {
        'employee_number': employee_number,
        'payments': payment_data,
    }


def calc_system_values(conn, pid, year, month, shabbat_cache, minimum_wage,
                       export_config, reports):
    """חישוב ערכי המערכת עבור מדריך."""
    try:
        daily, _ = get_daily_segments_data(
            conn, pid, year, month, shabbat_cache, minimum_wage,
            preloaded_reports=reports
        )
        totals = aggregate_daily_segments_to_monthly(
            conn, daily, pid, year, month, minimum_wage
        )

        system_values = {}
        for symbol, (internal_key, value_type, display_name) in export_config.items():
            qty, rate = calculate_value(totals, internal_key, value_type, minimum_wage)
            system_values[symbol] = {'qty': qty, 'rate': rate}

        return system_values, totals
    except Exception as e:
        print(f"  Error calculating pid={pid}: {e}")
        return None, None


def main() -> None:
    year, month = 2025, 11
    pdf_path = Path(__file__).resolve().parent.parent / "תלושים.pdf"
    out_path = Path(__file__).parent / "pdf_comparison_result.txt"

    doc = fitz.open(str(pdf_path))

    # חילוץ
    pdf_data = {}
    for i in range(len(doc)):
        page_data = extract_page_data(doc[i])
        emp_num = page_data['employee_number']
        if emp_num:
            pdf_data[str(emp_num)] = {
                'page': i + 1,
                'payments': page_data['payments'],
            }

    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)
        export_config = load_export_config_from_db(conn)

        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates "
            "ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        guides = conn.execute("""
            SELECT DISTINCT p.id, p.name, p.meirav_code
            FROM people p
            JOIN time_reports tr ON tr.person_id = p.id
            WHERE tr.date >= %s AND tr.date < %s
            ORDER BY p.name
        """, (f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01')).fetchall()

        results = []
        matched = 0

        for guide in guides:
            pid = guide["id"]
            name = guide["name"]
            emp_code = guide["meirav_code"]

            if not emp_code or emp_code not in pdf_data:
                continue

            matched += 1
            pdf_entry = pdf_data[emp_code]

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

            sys_values, totals = calc_system_values(
                conn, pid, year, month, shabbat_cache, minimum_wage,
                export_config, reports
            )

            if sys_values is None:
                continue

            diffs = []

            # 1. השוואת שעות (קודים עם כמות)
            for code, key in HOUR_CODES.items():
                pdf_val = pdf_entry['payments'].get(code, {})
                sys_val = sys_values.get(code, {})
                pdf_qty = pdf_val.get('qty', 0)
                sys_qty = sys_val.get('qty', 0)

                if abs(pdf_qty) < 0.01 and abs(sys_qty) < 0.01:
                    continue
                if abs(pdf_qty - sys_qty) > 0.02:
                    diffs.append((code, key, pdf_qty, sys_qty))

            # 2. כוננות (373) - rate = total payment
            pdf_sb = pdf_entry['payments'].get('373', {})
            sys_sb = sys_values.get('373', {})
            pdf_sb_rate = pdf_sb.get('rate', 0)
            sys_sb_rate = sys_sb.get('rate', 0)
            if (abs(pdf_sb_rate) > 0.01 or abs(sys_sb_rate) > 0.01) and abs(pdf_sb_rate - sys_sb_rate) > 1.0:
                diffs.append(('373', 'standby', pdf_sb_rate, sys_sb_rate))

            # 3. calc_variable (363) - qty=1 means exists
            pdf_cv = pdf_entry['payments'].get('363', {})
            sys_cv = sys_values.get('363', {})
            pdf_cv_qty = pdf_cv.get('qty', 0)
            sys_cv_qty = sys_cv.get('qty', 0)
            if abs(pdf_cv_qty - sys_cv_qty) > 0.5:
                diffs.append(('363', 'variable', pdf_cv_qty, sys_cv_qty))

            # 4. נסיעות (370) - amount
            pdf_tr = pdf_entry['payments'].get('370', {})
            sys_tr = sys_values.get('370', {})
            # בתלוש: amount = qty * rate, או amount ישיר
            pdf_tr_amt = pdf_tr.get('amount', 0) or (pdf_tr.get('qty', 0) * pdf_tr.get('rate', 0))
            sys_tr_amt = sys_tr.get('rate', 0)  # money type
            if (abs(pdf_tr_amt) > 0.01 or abs(sys_tr_amt) > 0.01) and abs(pdf_tr_amt - sys_tr_amt) > 1.0:
                diffs.append(('370', 'travel', pdf_tr_amt, sys_tr_amt))

            if diffs:
                results.append({
                    'name': name,
                    'emp': emp_code,
                    'page': pdf_entry['page'],
                    'diffs': diffs,
                })

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== PDF vs System comparison {month:02d}/{year} ===\n")
        f.write(f"Matched: {matched} / {len(guides)}\n\n")

        if not results:
            f.write("No discrepancies found!\n")
        else:
            f.write(f"Discrepancies in {len(results)} guides:\n\n")
            for r in results:
                f.write(f"--- {r['name']} (emp={r['emp']}, page={r['page']}) ---\n")
                for code, key, pdf_v, sys_v in r['diffs']:
                    diff = sys_v - pdf_v
                    f.write(f"  {code} ({key}): PDF={pdf_v:.2f} SYS={sys_v:.2f} diff={diff:+.2f}\n")
                f.write("\n")

        f.write(f"\nClean: {matched - len(results)}/{matched}\n")

    print(f"Results written to {out_path}")
    for r in results:
        codes = ", ".join(f"{c}({k})" for c, k, _, _ in r['diffs'])
        print(f"  {r['name']}: {codes}")


if __name__ == "__main__":
    main()
