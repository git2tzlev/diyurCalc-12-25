"""
Gesher File Exporter
מייצא קובץ בפורמט גשר למערכת מירב
"""
import io
import configparser
import logging
import calendar
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

from core.constants import TZOHAR_HALEV_HOUSING_ARRAY_ID
from core.database import get_housing_array_filter, get_multi_housing_guides
from core.history import get_minimum_wage_for_month

logger = logging.getLogger(__name__)

# נתיב לקובץ התצורה
CONFIG_PATH = Path(__file__).parent / "gesher_config.ini"

# קודים שלא לייצא לקובץ גשר בשום מצב
EXCLUDED_EXPORT_CODES = {'130', '199'}

COMPLETION_EXPORT_CODES = {
    "253": ("completion_non_pension", "money", "הפרשי השלמות לא לפנסיה"),
    "317": ("completion_pension", "money", "הפרשי השלמות לפנסיה"),
}

COMPLETION_TOTAL_KEYS = {
    "253": "completion_non_pension",
    "317": "completion_pension",
}


def with_completion_export_codes(export_codes: Dict[str, Tuple[str, str, str]]) -> Dict[str, Tuple[str, str, str]]:
    """Return export codes with virtual completion-difference symbols for preview display."""
    result = dict(export_codes)
    for symbol, value_tuple in COMPLETION_EXPORT_CODES.items():
        result.setdefault(symbol, value_tuple)
    return result


def append_completion_rows_to_preview(preview: List[Dict], completion_rows: List[Dict]) -> List[Dict]:
    """Append approved completion Gesher rows to the per-person preview cards."""
    if not completion_rows:
        return preview

    by_person_id = {
        person.get("person_id"): person
        for person in preview
        if person.get("person_id") is not None
    }
    by_employee_code = {
        "".join(ch for ch in str(person.get("meirav_code") or "") if ch.isdigit()).zfill(6): person
        for person in preview
        if person.get("meirav_code")
    }

    for row in completion_rows:
        person = by_person_id.get(row.get("person_id"))
        employee_code = "".join(ch for ch in str(row.get("employee_code") or "") if ch.isdigit()).zfill(6)
        if person is None and employee_code:
            person = by_employee_code.get(employee_code)
        if person is None:
            person = {
                "person_id": row.get("person_id"),
                "name": row.get("person_name") or "",
                "meirav_code": employee_code,
                "lines": [],
            }
            preview.append(person)
            if row.get("person_id") is not None:
                by_person_id[row.get("person_id")] = person
            if employee_code:
                by_employee_code[employee_code] = person

        symbol = str(row.get("symbol") or "")
        key, value_type, _ = COMPLETION_EXPORT_CODES.get(symbol, ("completion_difference", "money", ""))
        if value_type == "money":
            quantity = 0.0
            payment = round(float(row.get("amount") or 0), 2)
        else:
            quantity = round(float(row.get("quantity") or 0), 2)
            payment = 0.0

        person.setdefault("lines", []).append({
            "symbol": symbol,
            "key": key,
            "display_name": row.get("display_name") or "הפרשי השלמות",
            "type": value_type,
            "quantity": quantity,
            "payment": payment,
            "is_completion_difference": True,
            "source_symbols": row.get("source_symbols") or "",
        })

    return preview


def _completion_quantity_and_rate(row: Dict[str, Any]) -> tuple[float, float]:
    symbol = str(row.get("symbol") or "")
    value_type = COMPLETION_EXPORT_CODES.get(symbol, ("", "money", ""))[1]
    if value_type == "money":
        return 0.0, round(float(row.get("amount") or 0), 2)
    return round(float(row.get("quantity") or 0), 2), 0.0


def _clean_employee_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(6) if digits else ""


def empty_completion_totals() -> Dict[str, float]:
    return {
        "completion_non_pension": 0.0,
        "completion_pension": 0.0,
        "completion_retro_money_total": 0.0,
    }


def add_completion_row_to_totals(totals: Dict[str, Any], row: Dict[str, Any]) -> None:
    symbol = str(row.get("symbol") or "")
    key = COMPLETION_TOTAL_KEYS.get(symbol)
    if not key:
        return
    value_type = COMPLETION_EXPORT_CODES.get(symbol, ("", "money", ""))[1]
    if value_type == "money":
        value = round(float(row.get("amount") or 0), 2)
        totals[key] = round(float(totals.get(key) or 0) + value, 2)
        totals["completion_retro_money_total"] = round(
            float(totals.get("completion_retro_money_total") or 0) + value,
            2,
        )
        for total_key in ("total_payment", "gesher_total", "display_total", "rounded_total"):
            if total_key in totals:
                totals[total_key] = round(float(totals.get(total_key) or 0) + value, 2)
    else:
        value = round(float(row.get("quantity") or 0), 2)
        totals[key] = round(float(totals.get(key) or 0) + value, 2)


def apply_completion_rows_to_monthly_totals(
    totals: Dict[str, Any],
    completion_rows: List[Dict[str, Any]],
    *,
    person_id: int | None = None,
    employee_code: str | None = None,
) -> None:
    for key, value in empty_completion_totals().items():
        totals.setdefault(key, value)

    clean_code = _clean_employee_code(employee_code)
    for row in completion_rows:
        row_code = _clean_employee_code(row.get("employee_code"))
        person_match = person_id is not None and row.get("person_id") == person_id
        code_match = bool(clean_code and row_code == clean_code)
        if person_id is None and not clean_code:
            person_match = True
        if person_match or code_match:
            add_completion_row_to_totals(totals, row)


def apply_completion_rows_to_summary_data(
    summary_data: List[Dict[str, Any]],
    grand_totals: Dict[str, Any],
    completion_rows: List[Dict[str, Any]],
) -> None:
    seen_person_ids = set()
    seen_employee_codes = set()
    for person_data in summary_data:
        person_id = person_data.get("person_id") or person_data.get("id")
        employee_code = person_data.get("merav_code") or person_data.get("meirav_code")
        if person_id is not None:
            seen_person_ids.add(person_id)
        clean_code = _clean_employee_code(employee_code)
        if clean_code:
            seen_employee_codes.add(clean_code)
        apply_completion_rows_to_monthly_totals(
            person_data.setdefault("totals", {}),
            completion_rows,
            person_id=person_id,
            employee_code=employee_code,
        )

    completion_only: dict[tuple[Any, str], Dict[str, Any]] = {}
    for row in completion_rows:
        person_id = row.get("person_id")
        employee_code = _clean_employee_code(row.get("employee_code"))
        if (person_id is not None and person_id in seen_person_ids) or (
            employee_code and employee_code in seen_employee_codes
        ):
            continue
        key = (person_id, employee_code)
        if key not in completion_only:
            completion_only[key] = {
                "name": row.get("person_name") or "",
                "person_id": person_id,
                "merav_code": employee_code,
                "totals": empty_completion_totals(),
            }
        add_completion_row_to_totals(completion_only[key]["totals"], row)
    summary_data.extend(completion_only.values())

    for key, value in empty_completion_totals().items():
        grand_totals[key] = value
    for row in completion_rows:
        add_completion_row_to_totals(grand_totals, row)


def _write_completion_rows(
    output: io.StringIO,
    rows: List[Dict[str, Any]],
    *,
    filter_name: str | None = None,
    person_ids: set[int] | None = None,
    employee_codes: set[str] | None = None,
) -> int:
    line_count = 0
    for row in rows:
        person_name = row.get("person_name") or ""
        if filter_name and filter_name.lower() not in person_name.lower():
            continue
        employee_code_text = "".join(ch for ch in str(row.get("employee_code") or "") if ch.isdigit()).zfill(6)
        if not employee_code_text:
            continue
        if person_ids is not None or employee_codes is not None:
            person_match = person_ids is not None and row.get("person_id") in person_ids
            code_match = employee_codes is not None and employee_code_text in employee_codes
            if not person_match and not code_match:
                continue

        quantity, rate = _completion_quantity_and_rate(row)
        line = format_gesher_line(
            employee_code=int(employee_code_text),
            symbol=str(row["symbol"]),
            quantity=quantity,
            rate=rate,
        )
        output.write(line + "\r\n")
        line_count += 1
    return line_count


def should_block_multi_housing_for_gesher(housing_filter: int | None) -> bool:
    """בצוהר הלב לא מייצאים לגשר מדריך שפעיל ביותר ממערך דיור באותו חודש."""
    return housing_filter == TZOHAR_HALEV_HOUSING_ARRAY_ID


def get_blocked_multi_housing_for_gesher(
    conn,
    year: int,
    month: int,
    person_ids: List[int] | None = None,
) -> dict[int, list[str]]:
    """מדריכים שיש לחסום מייצוא גשר במערך צוהר הלב בגלל פעילות בכמה מערכים."""
    housing_filter = get_housing_array_filter()
    if not should_block_multi_housing_for_gesher(housing_filter):
        return {}

    start_date = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = date(year, month, last_day) + timedelta(days=1)
    blocked = get_multi_housing_guides(conn, start_date, end_date)
    if person_ids is not None:
        allowed = set(person_ids)
        blocked = {pid: arrays for pid, arrays in blocked.items() if pid in allowed}
    return blocked


def get_blocked_multi_housing_reason(arrays: list[str]) -> str:
    arrays_text = ", ".join(arrays)
    return f"המדריך פעיל בחודש זה ביותר ממערך דיור אחד: {arrays_text}"



def load_export_config_from_db(conn) -> Dict[str, Tuple[str, str, str]]:
    """
    טוען את תצורת הייצוא ממסד הנתונים (טבלת payment_codes).
    מחזיר: {symbol: (internal_key, value_type, display_name)}
    """
    try:
        rows = conn.execute("""
            SELECT internal_key, merav_code, display_name 
            FROM payment_codes 
            WHERE merav_code IS NOT NULL AND merav_code != ''
            ORDER BY display_order ASC NULLS LAST
        """).fetchall()
        
        export_codes = {}
        
        # מיפוי סוגי נתונים ברירת מחדל
        # מפתח פנימי -> סוג ייצוא
        type_mapping = {
            # שעות רגילות ונוספות
            'calc100': 'hours_100',
            'calc125': 'hours_125',
            'calc150': 'hours_150',
            'calc150_overtime': 'hours_150',
            
            # שעות שבת - מפוצל לפנסיה
            'calc150_shabbat_100': 'hours_100',
            'calc150_shabbat_50': 'hours_50',
            'calc175': 'hours_175',
            'calc200': 'hours_200',
            
            # כוננויות - כמות ותעריף ממוצע
            'standby': 'standby_with_rate',
            'standby_payment': 'money',  # לא בשימוש אם משתמשים ב-standby_with_rate
            
            # חופשה (מדקות)
            'vacation': 'hours_100',
            'vacation_minutes': 'hours_100',

            # מחלה - תשלום מחושב עם אחוזים מדורגים, מציג שעות משולמות
            'sick_payment': 'sick_hours_paid',
            'sick_minutes': 'hours_100',

            # תעריפים משתנים (ליווי רפואי וכדומה) - תשלום מחושב כולל בונוס
            'calc_variable': 'variable_rate_payment',

            # סכומים ישירים
            'travel': 'money',
            'professional_support': 'money',
            'holiday_payment': 'money',
            'recovery_pay': 'money',
            'extras': 'money',
            'extras_for_pension': 'money',
            
            # נתונים אינפורמטיביים
            'actual_work_days': 'days_with_total_hours',  # ימים + סה"כ שעות
            'sick_days_accrued': 'days',      # ימים
            'vacation_days_accrued': 'days',  # ימים
            'vacation_days_taken': 'days',    # ימים
            'sick_days_taken': 'days'         # ימים
        }
        
        for row in rows:
            internal_key = row['internal_key']
            symbol = row['merav_code']
            display_name = row['display_name'] or internal_key
            
            # אם אין סמל, מדלגים
            if not symbol or not symbol.strip():
                continue

            # קביעת הסוג
            # 1. בדיקה במיפוי הקשיח
            value_type = type_mapping.get(internal_key)
            
            # 2. אם לא נמצא, ננסה לנחש לפי המפתח
            if not value_type:
                if 'hours' in internal_key or 'calc' in internal_key:
                    value_type = 'hours_100'
                elif 'days' in internal_key:
                    value_type = 'days'
                elif 'payment' in internal_key or 'travel' in internal_key or 'extras' in internal_key:
                    value_type = 'money'
                else:
                    value_type = 'count'
            
            export_codes[symbol] = (internal_key, value_type, display_name)

        # מיון לפי מספר סמל (360, 361, 362...)
        export_codes = dict(sorted(
            export_codes.items(),
            key=lambda x: int(x[0]) if x[0].isdigit() else float('inf')
        ))

        return export_codes
    except Exception as e:
        logger.error("Error loading export config from DB: %s", e)
        return {} # Fallback to empty or file config if needed

def load_export_config() -> Dict[str, Tuple[str, str, str]]:
    """
    Legacy: loads from INI file.
    Kept for backward compatibility if needed, but generate_gesher_file will use DB version.
    """
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding='utf-8')
    
    export_codes = {}
    if 'EXPORT_CODES' in config:
        for symbol, value in config['EXPORT_CODES'].items():
            parts = [p.strip() for p in value.split(',')]
            if len(parts) >= 2:
                internal_key = parts[0]
                value_type = parts[1]  # hours, money, days, count
                display_name = internal_key  # אין שם עברי ב-INI
                export_codes[symbol] = (internal_key, value_type, display_name)
    
    return export_codes


def get_export_options() -> Dict[str, Any]:
    """טוען אפשרויות ייצוא"""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding='utf-8')
    
    options = {
        'export_zero_values': False,
        'min_amount': 0.01,
        'default_company': '001'
    }
    
    if 'OPTIONS' in config:
        options['export_zero_values'] = config.getboolean('OPTIONS', 'export_zero_values', fallback=False)
        options['min_amount'] = config.getfloat('OPTIONS', 'min_amount', fallback=0.01)
    
    if 'FORMAT' in config:
        options['default_company'] = config.get('FORMAT', 'default_company', fallback='001')
    
    return options


def get_companies(conn) -> Dict[str, str]:
    """טוען רשימת מפעלים מטבלת employers במסד הנתונים"""
    companies = {}
    try:
        rows = conn.execute("SELECT code, name FROM employers WHERE is_active::integer = 1").fetchall()
        for row in rows:
            companies[row['code']] = row['name']
    except Exception as e:
        logger.error("Error loading companies from DB: %s", e)
    
    return companies


def calculate_value(totals: Dict, internal_key: str, value_type: str, minimum_wage: float = 34.40) -> Tuple[float, float]:
    """
    מחשב את הערך לייצוא - מחזיר (כמות, תעריף)
    
    hours_XXX - שעות עם תעריף XXX% (מחזיר שעות ותעריף לשעה)
    money - סכום ישיר (מחזיר 0 וסכום)
    days - ימים (מחזיר ימים ו-0)
    count - ספירה (מחזיר כמות ו-0)
    standby_with_rate - כוננויות (כמות ותעריף ממוצע)
    """
    raw_value = totals.get(internal_key, 0) or 0
    
    if value_type == 'money':
        # סכום ישיר - אין כמות, רק סכום
        return (0.0, round(raw_value, 2))
    
    elif value_type.startswith('hours_'):
        # שעות עם תעריף - מחזיר שעות ותעריף לשעה
        multiplier_str = value_type.replace('hours_', '')
        try:
            multiplier = float(multiplier_str) / 100  # hours_100 -> 1.0, hours_125 -> 1.25
        except ValueError:
            multiplier = 1.0
        hours = round(raw_value / 60, 2)
        # שעות עבודה (calc*) - שימוש בתעריף בסיס ממוצע לרכיב הספציפי
        if internal_key.startswith('calc'):
            base_rate = (totals.get('component_base_rates') or {}).get(
                internal_key,
                totals.get('average_base_rate', minimum_wage),
            )
        else:
            base_rate = minimum_wage
        hourly_rate = round(base_rate * multiplier, 2)
        return (hours, hourly_rate)
    
    elif value_type == 'days':
        # ימים - לא לחלק ב-60!
        return (round(raw_value, 2), 0.0)
    
    elif value_type == 'count':
        # ספירה - לא לחלק ב-60!
        return (round(raw_value, 2), 0.0)
    
    elif value_type == 'days_with_total_hours':
        # ימי עבודה - כמות = ימים, תעריף = סה"כ שעות
        days = round(raw_value, 2)
        # נחשב סה"כ שעות מה-calc שדות
        total_hours = (
            totals.get('calc100', 0) + 
            totals.get('calc125', 0) + 
            totals.get('calc150', 0) + 
            totals.get('calc175', 0) + 
            totals.get('calc200', 0)
        ) / 60  # ממירות לשעות
        return (days, round(total_hours, 2))
    
    elif value_type == 'standby_with_rate':
        # כוננויות - תמיד כמות 1, תעריף = סכום כולל
        standby_payment = totals.get('standby_payment', 0) or 0
        if standby_payment > 0:
            return (1.0, round(standby_payment, 2))
        else:
            return (0.0, 0.0)

    elif value_type == 'sick_hours_paid':
        # תשלום מחלה - שעות משולמות (תשלום / תעריף) עם תעריף 100%
        sick_payment = totals.get('sick_payment', 0) or 0
        if sick_payment > 0 and minimum_wage > 0:
            paid_hours = round(sick_payment / minimum_wage, 2)
            return (paid_hours, round(minimum_wage, 2))
        else:
            return (0.0, 0.0)

    elif value_type == 'variable_rate_payment':
        # תעריפים משתנים - כמות=1, תעריף=סה"כ התשלום
        # כך נמנעים מפערי עיגול (כמות × תעריף = סכום מדויק)
        payment = totals.get('payment_calc_variable', 0) or 0
        if payment > 0:
            return (1.0, round(payment, 2))
        else:
            return (0.0, 0.0)

    else:
        # ברירת מחדל - כמות בלבד
        return (round(raw_value, 2), 0.0)


def get_minimum_wage(conn, year: int, month: int) -> float:
    """שליפת שכר מינימום היסטורי לפי חודש."""
    raw_conn = conn.conn if hasattr(conn, 'conn') else conn
    return get_minimum_wage_for_month(raw_conn, year, month)


def format_gesher_header(company: str, year: int, month: int) -> str:
    """
    פורמט כותרת קובץ גשר
    מבנה: מס' מפעל(3) + רווח + שנה(2) + רווח + חודש(2) + רזרבה
    """
    yy = str(year)[-2:]
    mm = f"{month:02d}"
    return f"{company:>3s} {yy} {mm}      0"


def format_gesher_line(employee_code: int, symbol: str, quantity: float, rate: float) -> str:
    """
    פורמט שורת נתונים בקובץ גשר
    
    מבנה רשומת נתונים:
    Pos 1-6:   מספר עובד (6 תווים)
    Pos 8-10:  מספר סמל (3 תווים)
    Pos 12-18: כמות (7 תווים, XXXX.XX)
    Pos 20-27: תעריף (8 תווים, XXXXX.XX)
    Pos 29-37: רווחים
    Pos 38-40: סיומת (201)
    """
    emp = f"{employee_code:06d}"           # 6 chars
    sym = f"{symbol:>3s}"                  # 3 chars
    qty = f"{quantity:07.2f}"              # 7 chars (XXXX.XX)
    rt = f"{rate:08.2f}"                   # 8 chars (XXXXX.XX)
    
    # פורמט: 005835 360 0020.50 00034.30          201
    line = f"{emp} {sym} {qty} {rt}          201"
    return line


def generate_gesher_file_for_person(conn, person_id: int, year: int, month: int) -> Tuple[str, str]:
    """
    מייצר קובץ גשר לעובד בודד
    
    Args:
        conn: חיבור למסד הנתונים
        person_id: מזהה עובד
        year: שנה
        month: חודש
    
    Returns:
        Tuple[תוכן הקובץ, קוד מפעל]
    """
    from core.logic import calculate_monthly_summary
    
    # שליפת פרטי העובד כולל מפעל
    person = conn.execute("""
        SELECT p.id, p.name, p.meirav_code, e.code as employer_code
        FROM people p
        LEFT JOIN employers e ON p.employer_id = e.id
        WHERE p.id = %s
    """, (person_id,)).fetchone()
    
    if not person or not person['meirav_code']:
        return ("", "")
    
    company = person['employer_code'] or '001'
    
    # טעינת תצורה
    export_codes = load_export_config_from_db(conn)
    if not export_codes:
        export_codes = load_export_config()
    options = get_export_options()
    
    minimum_wage = get_minimum_wage(conn, year, month)
    
    # וידוא קוד מירב
    try:
        meirav_code_clean = ''.join(filter(str.isdigit, str(person['meirav_code'])))
        if not meirav_code_clean:
            return ("", "")
        employee_code = int(meirav_code_clean)
    except ValueError:
        return ("", "")
    
    # חישוב דרך אותו מסלול של ייצוא גשר כללי כדי לכלול תשלום חג ורכיבים חודשיים זהים.
    raw_conn = conn.conn if hasattr(conn, 'conn') else conn
    summary_data, _ = calculate_monthly_summary(raw_conn, year, month)
    totals = {}
    for person_data in summary_data:
        pid = person_data.get('person_id') or person_data.get('id')
        if pid == person_id:
            totals = person_data.get('totals', {}) or {}
            break
    
    output = io.StringIO()
    
    # כותרת - CRLF
    header = format_gesher_header(company, year, month)
    output.write(header + "\r\n")
    
    line_count = 0
    
    # יצירת שורות
    for symbol, value_tuple in export_codes.items():
        # סינון קודים אסורים לייצוא (מוצגים בתצוגה מקדימה אבל לא בקובץ)
        if symbol in EXCLUDED_EXPORT_CODES:
            continue

        # תמיכה גם בפורמט ישן (2 איברים) וגם חדש (3 איברים)
        if len(value_tuple) == 3:
            internal_key, value_type, display_name = value_tuple
        else:
            internal_key, value_type = value_tuple

        quantity, rate = calculate_value(totals, internal_key, value_type, minimum_wage)
        
        if not options['export_zero_values']:
            if value_type.startswith('hours_') and quantity < options['min_amount']:
                continue
            elif value_type == 'money' and rate < options['min_amount']:
                continue
            elif quantity < options['min_amount'] and rate < options['min_amount']:
                continue
        
        line = format_gesher_line(
            employee_code=employee_code,
            symbol=symbol,
            quantity=quantity,
            rate=rate
        )
        output.write(line + "\r\n")
        line_count += 1

    from services.gesher_difference import build_approved_completion_gesher_rows

    completion_result = build_approved_completion_gesher_rows(
        conn,
        year,
        month,
        company_code=company,
        housing_array_id=get_housing_array_filter(),
    )
    line_count += _write_completion_rows(
        output,
        completion_result["rows"],
        person_ids={person_id},
        employee_codes={meirav_code_clean.zfill(6)},
    )
    
    result = output.getvalue()
    logger.info("Gesher export for person %s: %s lines", person_id, line_count)
    return (result, company)


def generate_gesher_file(
    conn,
    year: int,
    month: int,
    filter_name: str = None,
    company: str = None,
    *,
    allow_unverified_missing_final_completions: bool = False,
) -> str:
    """
    מייצר קובץ גשר לייצוא למירב
    משתמש ב-calculate_monthly_summary לחישוב יעיל של כל העובדים בבת אחת

    Args:
        conn: חיבור למסד הנתונים
        year: שנה
        month: חודש
        filter_name: סינון לפי שם עובד (אופציונלי, לבדיקות)
        company: קוד מפעל (001 או 400)

    Returns:
        תוכן הקובץ כמחרוזת
    """
    from core.logic import calculate_monthly_summary

    # טעינת תצורה מהדאטאבייס
    export_codes = load_export_config_from_db(conn)

    # אם אין הגדרות ב-DB, ננסה לטעון מהקובץ כגיבוי
    if not export_codes:
        export_codes = load_export_config()

    options = get_export_options()

    # קביעת מפעל
    if company is None:
        company = options.get('default_company', '001')

    minimum_wage = get_minimum_wage(conn, year, month)
    housing_filter = get_housing_array_filter()
    blocked_multi_housing = get_blocked_multi_housing_for_gesher(conn, year, month)
    from services.gesher_difference import CompletionGesherBlockedError, build_approved_completion_gesher_rows

    completion_result = build_approved_completion_gesher_rows(
        conn,
        year,
        month,
        company_code=company,
        housing_array_id=housing_filter,
        allow_unverified_missing_final=allow_unverified_missing_final_completions,
    )
    if completion_result["blocks"]:
        raise CompletionGesherBlockedError(completion_result["blocks"])

    # שליפת מיפוי עובדים למפעלים - עם סינון לפי מערך דיור אם מוגדר
    if housing_filter is not None:
        cursor = conn.execute("""
            SELECT p.id, p.name, p.meirav_code, e.code as employer_code
            FROM people p
            LEFT JOIN employers e ON p.employer_id = e.id
            WHERE p.is_active::integer = 1 AND p.meirav_code IS NOT NULL AND p.meirav_code != ''
              AND p.housing_array_id = %s
            ORDER BY p.name
        """, (housing_filter,))
    else:
        cursor = conn.execute("""
            SELECT p.id, p.name, p.meirav_code, e.code as employer_code
            FROM people p
            LEFT JOIN employers e ON p.employer_id = e.id
            WHERE p.is_active::integer = 1 AND p.meirav_code IS NOT NULL AND p.meirav_code != ''
            ORDER BY p.name
        """)
    all_people = {row['id']: row for row in cursor.fetchall()}

    # חישוב יעיל - כל העובדים בבת אחת
    raw_conn = conn.conn if hasattr(conn, 'conn') else conn
    summary_data, _ = calculate_monthly_summary(raw_conn, year, month)

    # בניית מיפוי person_id -> totals
    totals_by_id = {}
    for person_data in summary_data:
        pid = person_data.get('person_id') or person_data.get('id')
        if pid:
            totals_by_id[pid] = person_data.get('totals', {})

    output = io.StringIO()

    # כותרת - CRLF (Windows format: 0D 0A)
    header = format_gesher_header(company, year, month)
    output.write(header + "\r\n")

    line_count = 0

    for person_id, person in all_people.items():
        if person_id in blocked_multi_housing:
            logger.info(
                "Skipping person %s from Gesher export: %s",
                person_id,
                get_blocked_multi_housing_reason(blocked_multi_housing[person_id]),
            )
            continue

        # סינון לפי מפעל
        if person.get('employer_code') != company:
            continue

        # סינון לפי שם (אם נדרש)
        if filter_name and filter_name.lower() not in person['name'].lower():
            continue

        meirav_code = person['meirav_code']

        # וידוא שקוד מירב תקין
        try:
            meirav_code_clean = ''.join(filter(str.isdigit, str(meirav_code)))
            if not meirav_code_clean:
                continue
            employee_code = int(meirav_code_clean)
        except ValueError:
            continue

        # קבלת הסיכומים מה-cache
        totals = totals_by_id.get(person_id, {})

        # יצירת שורה לכל סמל
        for symbol, value_tuple in export_codes.items():
            # סינון קודים אסורים לייצוא (מוצגים בתצוגה מקדימה אבל לא בקובץ)
            if symbol in EXCLUDED_EXPORT_CODES:
                continue

            # תמיכה גם בפורמט ישן (2 איברים) וגם חדש (3 איברים)
            if len(value_tuple) == 3:
                internal_key, value_type, display_name = value_tuple
            else:
                internal_key, value_type = value_tuple

            quantity, rate = calculate_value(totals, internal_key, value_type, minimum_wage)

            # דילוג על ערכים אפסיים
            if not options['export_zero_values']:
                if value_type.startswith('hours_') and quantity < options['min_amount']:
                    continue
                elif value_type == 'money' and rate < options['min_amount']:
                    continue
                elif quantity < options['min_amount'] and rate < options['min_amount']:
                    continue

            # פורמט השורה לפי מבנה גשר - CRLF (Windows format: 0D 0A)
            line = format_gesher_line(
                employee_code=employee_code,
                symbol=symbol,
                quantity=quantity,
                rate=rate
            )
            output.write(line + "\r\n")
            line_count += 1

    line_count += _write_completion_rows(
        output,
        completion_result["rows"],
        filter_name=filter_name,
    )

    result = output.getvalue()
    logger.info("Gesher export: %s lines for company %s", line_count, company)
    return result


def generate_gesher_file_for_multiple(conn, person_ids: List[int], year: int, month: int) -> Tuple[str, str]:
    """
    מייצר קובץ גשר ממוזג לרשימת עובדים נבחרים

    Args:
        conn: חיבור למסד הנתונים
        person_ids: רשימת מזהי עובדים
        year: שנה
        month: חודש

    Returns:
        Tuple[תוכן הקובץ, קוד מפעל הראשון]
    """
    from core.logic import calculate_monthly_summary

    # טעינת תצורה
    export_codes = load_export_config_from_db(conn)
    if not export_codes:
        export_codes = load_export_config()
    options = get_export_options()
    minimum_wage = get_minimum_wage(conn, year, month)
    blocked_multi_housing = get_blocked_multi_housing_for_gesher(conn, year, month, person_ids)

    # שליפת פרטי העובדים
    if not person_ids:
        return ("", "")

    placeholders = ','.join(['?' if hasattr(conn, 'execute') else '%s'] * len(person_ids))
    cursor = conn.execute(f"""
        SELECT p.id, p.name, p.meirav_code, e.code as employer_code
        FROM people p
        LEFT JOIN employers e ON p.employer_id = e.id
        WHERE p.id IN ({placeholders})
        ORDER BY p.name
    """, tuple(person_ids))
    people_data = {row['id']: row for row in cursor.fetchall()}

    if not people_data:
        return ("", "")

    # קביעת מפעל הראשון (לשם הקובץ)
    first_company = None
    for pid in person_ids:
        if pid in people_data:
            first_company = people_data[pid].get('employer_code') or '001'
            break
    if not first_company:
        first_company = '001'

    # חישוב יעיל - כל העובדים בבת אחת
    raw_conn = conn.conn if hasattr(conn, 'conn') else conn
    summary_data, _ = calculate_monthly_summary(raw_conn, year, month)

    # בניית מיפוי person_id -> totals
    totals_by_id = {}
    for person_data in summary_data:
        pid = person_data.get('person_id') or person_data.get('id')
        if pid:
            totals_by_id[pid] = person_data.get('totals', {})

    output = io.StringIO()

    # כותרת - CRLF (Windows format)
    header = format_gesher_header(first_company, year, month)
    output.write(header + "\r\n")

    line_count = 0

    for person_id in person_ids:
        if person_id in blocked_multi_housing:
            logger.info(
                "Skipping person %s from selected Gesher export: %s",
                person_id,
                get_blocked_multi_housing_reason(blocked_multi_housing[person_id]),
            )
            continue

        if person_id not in people_data:
            continue

        person = people_data[person_id]
        meirav_code = person.get('meirav_code')

        if not meirav_code:
            continue

        # וידוא שקוד מירב תקין
        try:
            meirav_code_clean = ''.join(filter(str.isdigit, str(meirav_code)))
            if not meirav_code_clean:
                continue
            employee_code = int(meirav_code_clean)
        except ValueError:
            continue

        # קבלת הסיכומים מה-cache
        totals = totals_by_id.get(person_id, {})

        # יצירת שורה לכל סמל
        for symbol, value_tuple in export_codes.items():
            # סינון קודים אסורים לייצוא (מוצגים בתצוגה מקדימה אבל לא בקובץ)
            if symbol in EXCLUDED_EXPORT_CODES:
                continue

            # תמיכה גם בפורמט ישן (2 איברים) וגם חדש (3 איברים)
            if len(value_tuple) == 3:
                internal_key, value_type, display_name = value_tuple
            else:
                internal_key, value_type = value_tuple

            quantity, rate = calculate_value(totals, internal_key, value_type, minimum_wage)

            # דילוג על ערכים אפסיים
            if not options['export_zero_values']:
                if value_type.startswith('hours_') and quantity < options['min_amount']:
                    continue
                elif value_type == 'money' and rate < options['min_amount']:
                    continue
                elif quantity < options['min_amount'] and rate < options['min_amount']:
                    continue

            # פורמט השורה לפי מבנה גשר - CRLF (Windows format)
            line = format_gesher_line(
                employee_code=employee_code,
                symbol=symbol,
                quantity=quantity,
                rate=rate
            )
            output.write(line + "\r\n")
            line_count += 1

    from services.gesher_difference import build_approved_completion_gesher_rows

    completion_result = build_approved_completion_gesher_rows(
        conn,
        year,
        month,
        company_code=first_company,
        housing_array_id=get_housing_array_filter(),
    )
    selected_employee_codes = {
        "".join(filter(str.isdigit, str(person.get("meirav_code") or ""))).zfill(6)
        for person in people_data.values()
        if person.get("meirav_code")
    }
    line_count += _write_completion_rows(
        output,
        completion_result["rows"],
        person_ids=set(person_ids),
        employee_codes=selected_employee_codes,
    )

    result = output.getvalue()
    logger.info("Gesher export for %s selected people: %s lines", len(person_ids), line_count)
    return (result, first_company)


def get_export_preview(
    conn,
    year: int,
    month: int,
    limit: int = 50,
    summary_data: List[Dict] | None = None,
    completion_rows: List[Dict] | None = None,
) -> List[Dict]:
    """
    מחזיר תצוגה מקדימה של הייצוא
    משתמש ב-calculate_monthly_summary לחישוב יעיל של כל העובדים בבת אחת
    """
    from core.logic import calculate_monthly_summary

    # טעינת תצורה מהדאטאבייס
    export_codes = load_export_config_from_db(conn)

    # אם אין הגדרות ב-DB, ננסה לטעון מהקובץ כגיבוי
    if not export_codes:
        export_codes = load_export_config()

    options = get_export_options()
    minimum_wage = get_minimum_wage(conn, year, month)

    # חישוב יעיל - כל העובדים בבת אחת
    if summary_data is None:
        summary_data, _ = calculate_monthly_summary(
            conn.conn if hasattr(conn, 'conn') else conn, year, month
        )

    preview = []

    for person_data in summary_data:
        meirav_code = person_data.get('merav_code') or person_data.get('meirav_code')
        if not meirav_code:
            continue

        person_id = person_data.get('person_id') or person_data.get('id')
        person_name = person_data.get('name', '')
        totals = person_data.get('totals', {})

        person_lines = []
        for symbol, value_tuple in export_codes.items():
            if symbol in EXCLUDED_EXPORT_CODES:
                continue

            if len(value_tuple) >= 3:
                internal_key, value_type, display_name = value_tuple
            else:
                internal_key, value_type = value_tuple
                display_name = internal_key

            quantity, payment = calculate_value(totals, internal_key, value_type, minimum_wage)
            if quantity >= options['min_amount'] or payment >= options['min_amount']:
                person_lines.append({
                    'symbol': symbol,
                    'key': internal_key,
                    'display_name': display_name,
                    'type': value_type,
                    'quantity': quantity,
                    'payment': payment
                })

        if person_lines:
            preview.append({
                'person_id': person_id,
                'name': person_name,
                'meirav_code': meirav_code,
                'lines': person_lines
            })

    append_completion_rows_to_preview(preview, completion_rows or [])
    return preview


