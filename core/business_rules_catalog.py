"""
קטלוג כללים עסקיים גלוי למערכת.

המטרה היא לתעד את הכללים כפי שהם ממומשים בקוד בפועל, בצורה שאפשר להציג
בממשק ולתחזק ליד שינויי קוד. זה אינו מקור החישוב עצמו.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.constants import (
    ASD_NIGHT_STANDBY_RATE,
    ASD_HOUSING_ARRAY_ID,
    ASD_SENIORITY_SUPPLEMENT,
    ASD_SENIORITY_YEARS_THRESHOLD,
    APT_TYPE_NAMES,
    BERESHIT_APT_TYPE,
    BREAK_THRESHOLD_MINUTES,
    COMPLETION_APARTMENT_IDS,
    DEFAULT_STANDBY_RATE,
    FRIDAY_SHIFT_ID,
    HIGH_FUNCTIONING_APT_TYPE,
    HOLIDAY_PAY_MIN_SENIORITY_MONTHS,
    HOLIDAY_PAYMENT_INTERNAL_KEY,
    HOLIDAY_PAYMENT_MERAV_CODE,
    HOSPITAL_ESCORT_SHIFT_ID,
    KALANIYOT_APT_TYPE,
    LOW_FUNCTIONING_APT_TYPE,
    MAX_CANCELLED_STANDBY_DEDUCTION,
    MEDICAL_ESCORT_SHIFT_ID,
    MINIMUM_ESCORT_MINUTES,
    NIGHT_HOURS_END,
    NIGHT_HOURS_START,
    NIGHT_HOURS_THRESHOLD,
    NIGHT_SHIFT_ID,
    NIGHT_SHIFT_MORNING_END,
    NIGHT_SHIFT_STANDBY_END,
    NIGHT_SHIFT_WORK_FIRST_MINUTES,
    PERMANENT_EMPLOYEE_TYPE,
    REGULAR_APT_TYPE,
    SHABBAT_SHIFT_ID,
    SICK_SHIFT_TYPE_ID,
    STANDBY_CANCEL_OVERLAP_THRESHOLD,
    SUBSTITUTE_TRAVEL_TYPE_ID,
    TAGBUR_FRIDAY_PRE_ENTRY_MINUTES,
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_POST_EXIT_MINUTES,
    TAGBUR_SHABBAT_SHIFT_ID,
    THERAPEUTIC_APT_TYPE,
    VACATION_SHIFT_TYPE_ID,
    WEEKDAY_SHIFT_TYPE_ID,
    WEEKDAY_STANDBY_END,
    WEEKDAY_STANDBY_START,
    WORK_HOUR_SHIFT_ID,
)


@dataclass(frozen=True)
class BusinessRule:
    title: str
    summary: str
    details: tuple[str, ...] = ()
    source: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    status: str = "קוד קשיח"
    effective_from: str | None = None


@dataclass(frozen=True)
class BusinessRuleSection:
    key: str
    title: str
    description: str
    rules: tuple[BusinessRule, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BusinessRuleTable:
    key: str
    title: str
    description: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    source: tuple[str, ...] = ()


def _minutes_to_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


BUSINESS_RULE_TABLES: tuple[BusinessRuleTable, ...] = (
    BusinessRuleTable(
        key="shift-types",
        title="סוגי משמרות מרכזיים",
        description="מיפוי קודי המשמרות שהקוד מזהה באופן מפורש וההשפעה שלהם על החישוב.",
        columns=("קוד", "שם", "שימוש בחישוב", "הערות"),
        rows=(
            (
                str(WEEKDAY_SHIFT_TYPE_ID),
                "משמרת חול",
                "משמרת בסיס לעבודה רגילה, חופשה ומחלה לפי סוג דירה.",
                f"כוננות חול מוגדרת כברירת מחדל בין {_minutes_to_clock(WEEKDAY_STANDBY_START)} ל-{_minutes_to_clock(WEEKDAY_STANDBY_END)}.",
            ),
            (
                str(FRIDAY_SHIFT_ID),
                "משמרת שישי / ערב חג",
                "משמרת שבת/חג רגילה שנחתכת לפי זמני שבת או חג.",
                "בדירה טיפולית עם תעריף דירה רגילה היא מזוהה כתגבור משתמע.",
            ),
            (
                str(SHABBAT_SHIFT_ID),
                "משמרת שבת / חג",
                "משמרת שבת/חג רגילה שנכנסת לרצפים ולמדרגות שבת.",
                "גם היא יכולה להפוך לתגבור משתמע בדירה טיפולית עם תעריף רגיל.",
            ),
            (
                str(NIGHT_SHIFT_ID),
                "משמרת לילה",
                "בונה מקטעי עבודה וכוננות לילה ומפעילה ספי שעות לילה.",
                f"{NIGHT_SHIFT_WORK_FIRST_MINUTES // 60} שעות ראשונות עבודה, כוננות עד {_minutes_to_clock(NIGHT_SHIFT_STANDBY_END)}, עבודה עד {_minutes_to_clock(NIGHT_SHIFT_MORNING_END)}.",
            ),
            (
                str(TAGBUR_FRIDAY_SHIFT_ID),
                "תגבור שישי / ערב חג",
                "מקטעי תגבור סביב כניסת שבת או חג.",
                f"מצופה להתחיל {TAGBUR_FRIDAY_PRE_ENTRY_MINUTES} דקות לפני כניסה.",
            ),
            (
                str(TAGBUR_SHABBAT_SHIFT_ID),
                "תגבור שבת / חג",
                "מקטעי תגבור סביב יציאת שבת או חג.",
                f"מצופה להסתיים {TAGBUR_SHABBAT_POST_EXIT_MINUTES} דקות אחרי יציאה.",
            ),
            (
                str(WORK_HOUR_SHIFT_ID),
                "שעת עבודה",
                "משמשת לשעות שלא כוסו על ידי מקטע מוגדר.",
                "בחופשה/מחלה אין יצירת שעות לא מכוסות.",
            ),
            (
                str(HOSPITAL_ESCORT_SHIFT_ID),
                "ליווי בי\"ח",
                "משמרת ליווי ייעודית.",
                "נכנסת לחישוב לפי המקטעים/התעריפים המוגדרים עבורה.",
            ),
            (
                str(MEDICAL_ESCORT_SHIFT_ID),
                "ליווי רפואי",
                "משמרת ליווי עם מינימום תשלום.",
                f"דיווח קצר מ-{MINIMUM_ESCORT_MINUTES} דקות מקבל בונוס תשלום עד שעה.",
            ),
            (
                str(SICK_SHIFT_TYPE_ID),
                "מחלה",
                "דיווח היעדרות שמומר לתשלום מחלה ולצבירות.",
                "לא יוצר שעות לא מכוסות ולא נחשב להצעת מדריך קבוע לתשלום חג.",
            ),
            (
                str(VACATION_SHIFT_TYPE_ID),
                "חופשה",
                "דיווח היעדרות שמומר לתשלום חופשה ולצבירות.",
                "לא יוצר שעות לא מכוסות ולא נחשב להצעת מדריך קבוע לתשלום חג.",
            ),
        ),
        source=("core/constants.py", "app_utils.py:get_daily_segments_data", "core/shift_hours.py"),
    ),
    BusinessRuleTable(
        key="employee-types",
        title="סוגי מדריכים ועובדים",
        description="סוג העובד נשמר על person.type, עם היסטוריה חודשית כאשר יש שינוי סטטוס.",
        columns=("מפתח", "שם תצוגה", "איפה משפיע", "הערות"),
        rows=(
            (
                PERMANENT_EMPLOYEE_TYPE,
                "קבוע",
                "זכאות לתשלום חג, תוספת ותק ASD, רשימות דוחות.",
                f"תשלום חג דורש לפחות {HOLIDAY_PAY_MIN_SENIORITY_MONTHS} חודשי ותק, גם ב-ASD.",
            ),
            (
                "substitute",
                "מחליף",
                "רשימות דוחות, נסיעות מדריך מחליף ואישור אוטומטי.",
                f"נסיעות מסוג רכיב {SUBSTITUTE_TRAVEL_TYPE_ID} יכולות להתאשר אוטומטית אחרי אישור דיווחי היום.",
            ),
            (
                "אחר / ריק",
                "לא מוצג כמדריך פעיל בדוחות",
                "לא נכנס לרשימת דוחות חודשית רגילה.",
                "מסכי דוחות מסננים כיום לקבוע/מחליף בלבד.",
            ),
            (
                "unpaid holiday slot",
                "מדריך ללא תשלום חג",
                "ניהול תשלום חג בלבד.",
                "משבצת שמחלקת את תשלום החג לחצי בלי ליצור תשלום לעובד נוסף.",
            ),
        ),
        source=("core/history.py:get_person_status_for_month", "routes/reports.py:reports_page", "core/holiday_payment.py"),
    ),
    BusinessRuleTable(
        key="apartment-types",
        title="סוגי דירות ומערכים",
        description="סוג הדירה קובע תוספות, כללי היעדרות וחריגי תשלום חג.",
        columns=("קוד", "שם", "קבוצה עסקית", "השפעה מרכזית"),
        rows=(
            (
                str(REGULAR_APT_TYPE),
                APT_TYPE_NAMES[REGULAR_APT_TYPE],
                "רגיל",
                "ברירת מחדל של תעריף דירה ותשלום חג רגיל.",
            ),
            (
                str(THERAPEUTIC_APT_TYPE),
                APT_TYPE_NAMES[THERAPEUTIC_APT_TYPE],
                "טיפולי",
                "שישי/שבת עם תעריף דירה רגילה מזוהים כתגבור משתמע.",
            ),
            (
                str(BERESHIT_APT_TYPE),
                APT_TYPE_NAMES[BERESHIT_APT_TYPE],
                "היעדרות מיוחדת",
                "חג/חופשה/מחלה מחושבים לפי משמרת לילה ובקיזוז 70 ש\"ח בלבד מהכוננות.",
            ),
            (
                str(KALANIYOT_APT_TYPE),
                APT_TYPE_NAMES[KALANIYOT_APT_TYPE],
                "היעדרות מיוחדת",
                "חג/חופשה/מחלה מחושבים לפי משמרת חול ובקיזוז 70 ש\"ח בלבד מהכוננות.",
            ),
            (
                str(HIGH_FUNCTIONING_APT_TYPE),
                APT_TYPE_NAMES[HIGH_FUNCTIONING_APT_TYPE],
                "ASD",
                f"שייך לקבוצת ASD; כוננות לילה מסומנת יכולה לקבל {ASD_NIGHT_STANDBY_RATE:.0f} ש\"ח.",
            ),
            (
                str(LOW_FUNCTIONING_APT_TYPE),
                APT_TYPE_NAMES[LOW_FUNCTIONING_APT_TYPE],
                "ASD",
                "שייך לקבוצת ASD; כוננות לילה מסומנת יכולה להפוך לעבודה.",
            ),
            (
                ", ".join(str(apt_id) for apt_id in sorted(COMPLETION_APARTMENT_IDS)),
                "דירות השלמות",
                "תצוגה",
                "מופרדות בדוחות להצגת השלמות, לא כסוג תעריף עצמאי.",
            ),
            (
                str(ASD_HOUSING_ARRAY_ID),
                "מערך דיור ASD",
                "מערך",
                "כל דירה במערך הזה מקבלת חריג ASD בתשלום חג ותוספת ותק ASD.",
            ),
        ),
        source=("core/constants.py", "app_utils.py", "core/holiday_payment.py"),
    ),
    BusinessRuleTable(
        key="housing-arrays",
        title="הבדלים בין מערכי דיור",
        description="הבדלים עסקיים שמופעלים לפי housing_array_id, מעבר לסוג הדירה עצמו.",
        columns=("מערך", "זיהוי בקוד", "תעריפים", "תשלום חג", "לילה וותק", "הרשאות ותצוגה"),
        rows=(
            (
                "צוהר הלב",
                "מערך רגיל; כרגע id=1 ב-DB.",
                "תעריפים לפי shift_type_housing_rates לכל סוג משמרת. אם אין תעריף, fallback לשכר מינימום + תוספת סוג דירה.",
                "חלוקת חג רגילה: מדריך אחד מקבל מלא, שני מדריכים חצי-חצי, ומשבצת ללא תשלום מחלקת לחצי.",
                "אין תוספת ותק ASD ואין חריג ASD במשמרת לילה.",
                "מנהל מערך שמוגבל למערך זה רואה רק דירות/מדריכים של צוהר הלב.",
            ),
            (
                "ASD",
                f"מזוהה בקוד לפי housing_array_id={ASD_HOUSING_ARRAY_ID}.",
                "תעריפים לפי shift_type_housing_rates של ASD; עדיין יש fallback לשכר מינימום + תוספת סוג דירה אם חסר תעריף.",
                "חריג חג: כל מדריך זכאי מקבל חג מלא גם כשיש יותר ממדריך אחד בדירה; תנאי 3 חודשי ותק עדיין נשאר.",
                f"מדריך קבוע עם {ASD_SENIORITY_YEARS_THRESHOLD}+ שנת ותק מקבל תוספת {ASD_SENIORITY_SUPPLEMENT / 100:.0f} ש\"ח לשעה. בסימון לילה ASD יש הבדל בין תפקוד גבוה לתפקוד נמוך.",
                "מנהל מערך ASD רואה רק דירות/מדריכים של ASD; דוחות ורצפים מסמנים את שם המערך.",
            ),
        ),
        source=("core/constants.py:is_asd_housing_array", "app_utils.py", "core/holiday_payment.py", "core/auth.py"),
    ),
    BusinessRuleTable(
        key="thresholds",
        title="ספים, זמנים וקיזוזים קשיחים",
        description="ערכים מספריים שהקוד משתמש בהם כחלק מהחלטות שכר ורצפים.",
        columns=("כלל", "ערך", "משמעות", "מקור"),
        rows=(
            (
                "שבירת רצף",
                f"{BREAK_THRESHOLD_MINUTES} דקות",
                "הפסקה באורך הזה או יותר שוברת רצף מחודש 02/2026.",
                "core/constants.py",
            ),
            (
                "סף חפיפה לביטול כוננות",
                f"{STANDBY_CANCEL_OVERLAP_THRESHOLD:.0%}",
                "אם עבודה חופפת לפחות שיעור זה מהכוננות, הכוננות מתבטלת.",
                "core/constants.py",
            ),
            (
                "קיזוז מקסימלי בכוננות מבוטלת",
                f"{MAX_CANCELLED_STANDBY_DEDUCTION:.0f} ש\"ח",
                "בכוננות יקרה יותר משלמים רק את ההפרש מעל הקיזוז.",
                "core/constants.py",
            ),
            (
                "תעריף כוננות ברירת מחדל",
                f"{DEFAULT_STANDBY_RATE:.0f} ש\"ח",
                "משמש כאשר אין תעריף כוננות אחר.",
                "core/constants.py",
            ),
            (
                "סף משמרת לילה",
                f"{NIGHT_HOURS_THRESHOLD // 60} שעות",
                f"נדרשות לפחות {NIGHT_HOURS_THRESHOLD // 60} שעות בטווח {_minutes_to_clock(NIGHT_HOURS_START)}-{_minutes_to_clock(NIGHT_HOURS_END)}.",
                "core/constants.py",
            ),
            (
                "ותק מינימלי לתשלום חג",
                f"{HOLIDAY_PAY_MIN_SENIORITY_MONTHS} חודשים",
                "מדריך קבוע מתחת לסף לא מקבל תשלום חג.",
                "core/constants.py",
            ),
            (
                "תוספת ותק ASD",
                f"{ASD_SENIORITY_SUPPLEMENT / 100:.0f} ש\"ח אחרי {ASD_SENIORITY_YEARS_THRESHOLD} שנה",
                "נוספת לשעת עבודה של מדריך קבוע במערך ASD.",
                "core/constants.py",
            ),
            (
                "תגבור ערב",
                f"{TAGBUR_FRIDAY_PRE_ENTRY_MINUTES} דקות לפני כניסה",
                "גבול מצופה למשמרת תגבור שישי/ערב חג.",
                "core/constants.py",
            ),
            (
                "תגבור יציאה",
                f"{TAGBUR_SHABBAT_POST_EXIT_MINUTES} דקות אחרי יציאה",
                "גבול מצופה למשמרת תגבור שבת/חג.",
                "core/constants.py",
            ),
            (
                "מינימום ליווי רפואי",
                f"{MINIMUM_ESCORT_MINUTES} דקות",
                "בונוס תשלום עד שעה לדיווח קצר יותר.",
                "core/constants.py",
            ),
        ),
        source=("core/constants.py",),
    ),
    BusinessRuleTable(
        key="export-components",
        title="רכיבי שכר וייצוא גשר",
        description="מיפוי המפתחות הפנימיים לסוג הערך שנשלח לגשר. סמלי מירב עצמם מנוהלים בטבלת payment_codes.",
        columns=("מפתח פנימי", "סוג ייצוא", "מה נשלח", "הערות"),
        rows=(
            ("calc100", "hours_100", "שעות ותעריף בסיס", "שעות עבודה רגילות."),
            ("calc125", "hours_125", "שעות ותעריף 125%", "שעות נוספות מדרגה ראשונה."),
            ("calc150 / calc150_overtime", "hours_150", "שעות ותעריף 150%", "שבת/חג או שעות נוספות לפי הרצף."),
            ("calc150_shabbat_100", "hours_100", "שעות ותעריף בסיס", "פיצול שעות שבת לפנסיה."),
            ("calc150_shabbat_50", "hours_50", "שעות ותעריף 50%", "השלמת רכיב שבת מעל הבסיס."),
            ("calc175", "hours_175", "שעות ותעריף 175%", "מדרגת שבת/חג."),
            ("calc200", "hours_200", "שעות ותעריף 200%", "מדרגת שבת/חג או פרימיום 200%."),
            ("standby", "standby_with_rate", "כמות 1 ותעריף שהוא סכום הכוננות הכולל", "כך נבנית השורה בפועל בייצוא גשר."),
            ("vacation / vacation_minutes", "hours_100", "שעות ותעריף שכר מינימום", "תלוי בלוגיקת חופשה."),
            ("sick_payment", "sick_hours_paid", "שעות מחלה משולמות", "אחרי אחוזי מחלה מדורגים."),
            ("calc_variable", "variable_rate_payment", "כמות 1 וסכום", "נועד למנוע פערי עיגול בתעריף משתנה."),
            (HOLIDAY_PAYMENT_INTERNAL_KEY, "money", "סכום ישיר", f"סמל ברירת המחדל הידוע: {HOLIDAY_PAYMENT_MERAV_CODE}."),
            ("travel / extras / extras_for_pension / professional_support", "money", "סכום ישיר", "רכיבי תשלום ידניים."),
            ("actual_work_days", "days_with_total_hours", "ימים ותעריף שמייצג סה\"כ שעות", "נתון אינפורמטיבי לייצוא."),
            ("sick_days_* / vacation_days_*", "days", "ימים", "צבירות וניצולים."),
            ("130, 199", "מוחרג", "לא נשלח", "קודי מירב מוחרגים גם אם קיימים בתצוגה."),
        ),
        source=("services/gesher_exporter.py:load_export_config_from_db", "services/gesher_exporter.py:calculate_value"),
    ),
)


BUSINESS_RULE_SECTIONS: tuple[BusinessRuleSection, ...] = (
    BusinessRuleSection(
        key="scope-history",
        title="תוקף נתונים והרשאות",
        description="איזה חודש, מערך דיור ונתונים היסטוריים נכנסים לחישוב.",
        rules=(
            BusinessRule(
                title="שכר מינימום לפי חודש",
                summary="שכר המינימום נשלף מטבלת minimum_wage_rates לפי הרשומה האחרונה שתקפה בתחילת החודש.",
                details=(
                    "הערך נשמר באגורות ומומר לשקלים.",
                    "אם אין שכר מינימום תקף לחודש המבוקש, החישוב נכשל במפורש במקום להשתמש בערך משוער.",
                ),
                source=("core/history.py:get_minimum_wage_for_month",),
                tags=("שכר מינימום", "היסטוריה"),
                status="מנוהל בטבלת minimum_wage_rates",
            ),
            BusinessRule(
                title="סטטוס מדריך היסטורי",
                summary="נישואין, מעסיק וסוג עובד נקבעים לפי היסטוריה חודשית, ורק אם אין היסטוריה משתמשים בערך הנוכחי.",
                details=(
                    "רשומת היסטוריה נשמרת כ'תקף עד החודש הזה, לא כולל'.",
                    "הכלל משפיע על תעריפי נשוי/רווק, על קוד מפעל בגשר ועל זכאות של קבוע/מחליף.",
                ),
                source=("core/history.py:get_person_status_for_month", "core/history.py:get_all_person_statuses_for_month"),
                tags=("סטטוס עובד", "קבוע", "מחליף"),
            ),
            BusinessRule(
                title="סוג דירה היסטורי וסוג דירה לתשלום",
                summary="סוג הדירה לחישוב נלקח קודם מהדיווח עצמו, אחר כך מהיסטוריה חודשית, ורק בסוף מהדירה הנוכחית.",
                details=(
                    "rate_apartment_type_id בדיווח גובר על סוג הדירה ההיסטורי.",
                    "המערכת שומרת בנפרד סוג דירה בפועל לתצוגה וסוג דירה לתעריף.",
                    "תאריך שינוי סוג דירה מוצג בדוח הרצפים כאשר קיימת היסטוריה.",
                ),
                source=("app_utils.py:get_daily_segments_data", "core/history.py:get_apartment_type_for_month"),
                tags=("סוג דירה", "היסטוריה", "תעריף"),
            ),
            BusinessRule(
                title="סינון מנהל מערך",
                summary="מנהל מערך רואה ומחשב רק את מערך הדיור שלו; מנהל על עובד ללא סינון מערך.",
                details=(
                    "הסינון חל על דוחות, ניהול תשלום חג, ייצוא גשר וחלק משאילתות החישוב.",
                    "גישה למדריך מחוץ למערך של מנהל מערך נחסמת.",
                    "פער ידוע: בייצור PDF/מייל משולב יש מסלול שבו המדריכים נבחרים לפי מערך, אך הכנת נתוני הדוח נקראת בלי housing_filter.",
                ),
                source=("core/auth.py:get_user_housing_array", "core/auth.py:enforce_framework_manager_guide_access", "services/email_service.py:_generate_combined_guides_pdf"),
                tags=("הרשאות", "מנהל מערך"),
                status="הרשאה",
            ),
            BusinessRule(
                title="נעילת חודש",
                summary="חודש נעול נשמר בטבלת month_locks ומשמש את מסכי הניהול שחשופים כרגע לנעילה/פתיחה.",
                details=(
                    "נעילה נשמרת בטבלת month_locks.",
                    "אם unlocked_at מלא, החודש נחשב פתוח.",
                    "API נעילה ופתיחה דורשים מנהל על; סטטוס נעילה חשוף למשתמש מחובר.",
                    "קיימות פונקציות עדכון תעריפי משמרות ומקטעים שבודקות נעילה, אך הן אינן מחוברות כרגע כנתיבי app פעילים.",
                ),
                source=("core/history.py:is_month_locked", "routes/admin.py:get_month_lock_status", "routes/admin.py:lock_month_api", "routes/admin.py:unlock_month_api"),
                tags=("נעילת חודש", "ניהול"),
                status="מנוהל במסך ניהול",
            ),
        ),
    ),
    BusinessRuleSection(
        key="rates",
        title="תעריפים ובסיס שכר",
        description="איך נקבע תעריף שעה לפני חלוקה לאחוזים ולרצפים.",
        rules=(
            BusinessRule(
                title="תעריפי מערך דיור לפי משמרת",
                summary="תעריף משמרת נלקח מטבלת shift_type_housing_rates לפי shift_type_id ומערך דיור.",
                details=(
                    "יש תמיכה בתעריף קבוע לרווק, תעריף קבוע לנשוי, אחוז משכר מינימום ותעריף שבת.",
                    "כאשר יש היסטוריה חודשית, היא מחליפה את ההגדרה הנוכחית.",
                    "אם אין תעריף מוגדר, חוזרים לשכר מינימום בתוספת סוג דירה.",
                ),
                source=("core/history.py:get_all_housing_rates_for_month", "app_utils.py:calculate_rate_from_housing_rates"),
                tags=("תעריפים", "מערך דיור"),
                status="מנוהל במסך תעריפים",
            ),
            BusinessRule(
                title="תוספת סוג דירה",
                summary="כאשר אין תעריף מפורש, בסיס השכר הוא שכר מינימום ועוד תוספת סוג הדירה באגורות.",
                details=(
                    "התוספת נשמרת על apartment_types.hourly_wage_supplement.",
                    "בדוח הרצפים עמודת בסיס מציגה את התעריף הכולל ללא כפל תוספת.",
                ),
                source=("app_utils.py:get_effective_hourly_rate", "app_utils.py:_display_base_hourly"),
                tags=("סוג דירה", "בסיס"),
            ),
            BusinessRule(
                title="תוספת ותק ASD",
                summary=f"ב-ASD מדריך קבוע עם ותק של {ASD_SENIORITY_YEARS_THRESHOLD} שנה ומעלה מקבל תוספת {ASD_SENIORITY_SUPPLEMENT / 100:.0f} ש\"ח לשעה.",
                details=(
                    "התוספת חלה רק במערך דיור ASD.",
                    "התוספת מוזרקת לתעריף הבסיס בחישוב שעות העבודה.",
                    "הכלל אינו מחליף את תנאי הוותק של תשלום חג.",
                ),
                source=("app_utils.py:_get_asd_seniority_supplement", "core/constants.py"),
                tags=("ASD", "ותק", "תוספת"),
            ),
            BusinessRule(
                title="שיטת עיגול מירב",
                summary="תשלומים מחושבים לפי שעות מעוגלות לשתי ספרות, תעריף מעוגל לשתי ספרות, וסכום מעוגל לעשירית בשיטת ROUND_HALF_UP.",
                details=(
                    "העיגול נעשה ברמת רכיב/שורה ולא רק בסוף החודש.",
                    "הסך לתצוגה הוא סכום השורות המעוגלות כדי להתאים לתלוש/גשר.",
                ),
                source=("app_utils.py:_round_pay", "app_utils.py:_mul_pay", "app_utils.py:aggregate_daily_segments_to_monthly"),
                tags=("עיגול", "מירב", "גשר"),
            ),
            BusinessRule(
                title="תעריף משתנה",
                summary="שורות בתעריף מיוחד נצברות ברכיב calc_variable במקום להתערבב עם שעות שכר מינימום.",
                details=(
                    "מחוץ ל-ASD, משמרת עם is_special_hourly או תעריף שונה משכר מינימום+תוספת נחשבת תעריף משתנה.",
                    "ב-ASD כל השעות הופכות לתעריף משתנה רק אם בפועל יש יותר מתעריף בסיס אחד בחודש.",
                    "בייצוא גשר תעריף משתנה יוצא כסכום מדויק בכמות 1 כדי למנוע פערי עיגול.",
                ),
                source=("app_utils.py:aggregate_daily_segments_to_monthly", "services/gesher_exporter.py:calculate_value"),
                tags=("תעריף משתנה", "ASD", "גשר"),
            ),
        ),
    ),
    BusinessRuleSection(
        key="shift-segments",
        title="מקטעי משמרת ושעות",
        description="איך דיווח הופך לעבודה, כוננות, חופשה או מחלה.",
        rules=(
            BusinessRule(
                title="מקטעי משמרת",
                summary="דיווח נחתך לפי shift_time_segments; זמן שאינו מכוסה במקטע מוגדר הופך לשעת עבודה.",
                details=(
                    "המקטעים ממוינים לפי order_index ואז id.",
                    "כאשר משמרת חוצה חצות, הזמנים מנורמלים לציר יום עבודה 08:00-08:00.",
                    "דיווח ללא מקטעים מוגדרים נחשב כולו עבודה.",
                ),
                source=("core/shift_hours.py:calculate_segment_hours", "app_utils.py:get_daily_segments_data"),
                tags=("מקטעים", "08:00"),
            ),
            BusinessRule(
                title="משמרת לילה רגילה",
                summary=f"במשמרת לילה לא-ASD, {NIGHT_SHIFT_WORK_FIRST_MINUTES / 60:.0f} השעות הראשונות הן עבודה, אחר כך כוננות עד 06:30, ואז עבודה עד 08:00.",
                details=(
                    "הסגמנטים נבנים דינמית לפי שעת הכניסה והיציאה בפועל.",
                    "אם הדיווח מסתיים לפני סוף המקטע, המקטע נחתך לפי שעת הסיום.",
                ),
                source=("core/shift_hours.py:calculate_night_shift_hours", "app_utils.py:get_daily_segments_data"),
                tags=("לילה", "כוננות"),
            ),
            BusinessRule(
                title="חפיפה בשעתיים הראשונות של לילה",
                summary="מ-03/2026 דיווח עבודה שחופף לשעתיים הראשונות של לילה מבטל את התשלום הכפול על השעתיים האלה.",
                details=(
                    "הכלל מטפל במצב שבו יש גם משמרת לילה וגם שמירה/עבודה חופפת בתחילת הלילה.",
                    "הקיזוז נעשה לפני בניית הרצפים היומיים.",
                ),
                source=("app_utils.py:_trim_night_first_work_overlaps", "app_utils.py:get_daily_segments_data"),
                tags=("לילה", "חפיפה"),
                effective_from="03/2026",
            ),
            BusinessRule(
                title="ASD לילה",
                summary="סימון ASD לילה מפעיל הבחנה בין תפקוד גבוה לתפקוד נמוך במשמרות לילה.",
                details=(
                    "הכלל מופעל רק כאשר הדיווח מסומן כ-ASD night marking.",
                    f"תפקוד גבוה מקבל כוננות לילה בסך {ASD_NIGHT_STANDBY_RATE:.0f} ש\"ח.",
                    "תפקוד נמוך: מקטע standby בלילה הופך ל-work.",
                    "ללא סימון ASD לילה, משמרת ASD משתמשת במקטעי המשמרת הרגילים.",
                    "הדוח מסמן תוויות 'שינה בסלון' ו'ערות בלילה'.",
                ),
                source=("core/constants.py", "app_utils.py:get_daily_segments_data"),
                tags=("ASD", "לילה", "כוננות"),
            ),
            BusinessRule(
                title="שעות לא מכוסות",
                summary="זמן בדיווח שאינו חופף למקטע מוגדר נוסף כשעת עבודה.",
                details=(
                    "אם למשמרת יש מקטעים מוגדרים, השעות הלא מכוסות מקבלות את shift_type_id של 'שעת עבודה'.",
                    "אם למשמרת אין מקטעים מוגדרים, השעות נשארות בתעריף המשמרת המקורית.",
                    "בחופשה/מחלה אין שעות לא מכוסות.",
                ),
                source=("app_utils.py:get_daily_segments_data",),
                tags=("שעות לא מכוסות", "שעת עבודה"),
            ),
            BusinessRule(
                title="ליווי רפואי מינימום שעה",
                summary=f"משמרת ליווי רפואי קצרה מ-{MINIMUM_ESCORT_MINUTES} דקות מקבלת בונוס תשלום עד שעה.",
                details=(
                    "הבונוס הוא תשלום בלבד ואינו מוסיף דקות לרצף עבודה או לשעות נוספות.",
                    "הבונוס מצורף לשורת העבודה בדוח הרצפים כהערת תצוגה.",
                ),
                source=("app_utils.py:get_daily_segments_data", "core/constants.py"),
                tags=("ליווי רפואי", "בונוס"),
            ),
        ),
    ),
    BusinessRuleSection(
        key="chains",
        title="רצפים ושעות נוספות",
        description="חישוב רצפי עבודה, הפסקות, שבת/חג ופרימיום.",
        rules=(
            BusinessRule(
                title="יום עבודה 08:00-08:00",
                summary="דוחות הרצפים מסדרים משמרות לפי יום עבודה שמתחיל ב-08:00 ומסתיים ב-08:00 למחרת.",
                details=(
                    "שעות 00:00-07:59 מוצגות כהמשך יום העבודה הקודם אם הן המשך משמרת.",
                    "דיווח עצמאי שהתחיל אחרי חצות יכול להישאר ביום הנוכחי.",
                ),
                source=("app_utils.py:get_daily_segments_data",),
                tags=("רצפים", "08:00"),
            ),
            BusinessRule(
                title="שבירת רצף בהפסקה",
                summary=f"הפסקה של {BREAK_THRESHOLD_MINUTES} דקות או יותר שוברת רצף מחודש 02/2026 ואילך.",
                details=(
                    "לפני 02/2026 נשמרה לוגיקה היסטורית שבה רק הפסקה גדולה מ-60 דקות שברה רצף.",
                    "כאשר הרצף נשבר בהפסקה, שעות נוספות מתחילות מחדש.",
                ),
                source=("app_utils.py:get_daily_segments_data", "app_utils.py:_calculate_previous_month_carryover"),
                tags=("רצף", "הפסקה"),
                effective_from="02/2026",
            ),
            BusinessRule(
                title="כוננות ושבירת רצף",
                summary="כוננות שוברת רצף לפי החלק שנותר אחרי חפיפה עם עבודה.",
                details=(
                    "הקוד קודם מחסיר מקטעי עבודה מתוך הכוננות.",
                    "אם נותרו חלקי כוננות שאינם חופפים עבודה, הם יכולים לשבור את הרצף.",
                    "כוננות ללא חפיפה מאפסת את offset השעות הנוספות.",
                ),
                source=("app_utils.py:get_daily_segments_data", "app_utils.py:_calculate_previous_month_carryover"),
                tags=("כוננות", "רצף"),
            ),
            BusinessRule(
                title="שינוי תעריף בתוך רצף",
                summary="שינוי תעריף סוגר שורת רצף, אבל מעביר את offset השעות הנוספות להמשך.",
                details=(
                    "ההשוואה נעשית לפי תעריף חול של השורה.",
                    "הסיבה מוצגת בדוח הרצפים כ'שינוי תעריף'.",
                    "בניגוד להפסקה או כוננות, שינוי תעריף אינו מאפס את מדרגות השעות הנוספות.",
                ),
                source=("app_utils.py:get_daily_segments_data", "app_utils.py:_calculate_previous_month_carryover"),
                tags=("שינוי תעריף", "שעות נוספות"),
            ),
            BusinessRule(
                title="חפיפת שעות עבודה",
                summary="כאשר כמה דיווחי עבודה חופפים בזמן, החפיפה נחתכת ונשאר הדיווח עם התעריף השעתי הגבוה יותר.",
                details=(
                    "החיתוך נעשה לפני חישוב כוננויות ורצפים כדי למנוע תשלום כפול על אותו זמן.",
                    "אם התעריפים זהים, נבחר הדיווח הקצר/ספציפי יותר לאותו מקטע חופף.",
                    "בדוח הרצפים מוצגת התרעה על יום עם חפיפה.",
                ),
                source=("app_utils.py:_resolve_work_segment_overlaps", "app_utils.py:get_daily_segments_data"),
                tags=("חפיפה", "רצפים", "תעריף"),
            ),
            BusinessRule(
                title="רצף מחודש קודם",
                summary="אם היום האחרון של החודש הקודם ממשיך לרצף שמסתיים ב-08:00, offset הרצף עובר לחודש הנוכחי.",
                details=(
                    "המערכת מחפשת אחורה עד 31 ימים או עד יום ללא דיווחים.",
                    "חופשה/מחלה ללא שעות שוברת carryover מהחודש הקודם.",
                    "הcarryover כולל גם דקות לילה לצורך סף משמרת לילה.",
                    "פער ידוע: חישוב דקות הלילה ב-carryover החודש הקודם משתמש בנרמול שונה מהמסלול היומי.",
                ),
                source=("app_utils.py:_calculate_previous_month_carryover",),
                tags=("carryover", "חודש קודם"),
            ),
            BusinessRule(
                title="סף משמרת לילה לשעות נוספות",
                summary=f"רצף נחשב רצף לילה אם יש בו לפחות {NIGHT_HOURS_THRESHOLD / 60:.0f} שעות בטווח 22:00-06:00.",
                details=(
                    "רצף לילה משתמש בסף יומי של 7 שעות לפני 125%, במקום 8 שעות.",
                    "דקות לילה מ-carryover מצטרפות לסף.",
                ),
                source=("app_utils.py:get_daily_segments_data", "core/constants.py:qualifies_as_night_shift"),
                tags=("לילה", "שעות נוספות"),
            ),
            BusinessRule(
                title="מדרגות חול ושבת",
                summary="רצף חול מתחלק ל-100%, 125%, 150%; שבת/חג מתחלקים ל-150%, 175%, 200%.",
                details=(
                    "שבת וחג נקבעים לפי זמני כניסה/יציאה בפועל, לא רק לפי שם המשמרת.",
                    "150% שבת מפוצל בגשר ל-100% ול-50% כדי להתאים לפנסיה/מירב.",
                    "חלונות פרימיום יכולים לשנות את אחוז השכר בתוך אותו רצף.",
                ),
                source=("app_utils.py:_calculate_chain_wages", "app_utils.py:aggregate_daily_segments_to_monthly"),
                tags=("100%", "125%", "150%", "שבת"),
            ),
        ),
    ),
    BusinessRuleSection(
        key="standby",
        title="כוננות וקיזוזים",
        description="תשלום כוננות, ביטול כוננות וחפיפות מול עבודה.",
        rules=(
            BusinessRule(
                title="תעריף כוננות",
                summary=f"אם לא נמצא תעריף כוננות מוגדר, ברירת המחדל היא {DEFAULT_STANDBY_RATE:.0f} ש\"ח.",
                details=(
                    "עדיפות החיפוש: היסטוריה לסוג דירה, היסטוריה כללית, תעריף נוכחי לסוג דירה, תעריף נוכחי כללי.",
                    "תעריף נשוי/רווק נקבע לפי סטטוס המדריך ההיסטורי לאותו חודש.",
                ),
                source=("core/history.py:get_standby_rate_for_month", "app_utils.py:get_standby_rate"),
                tags=("כוננות", "תעריף"),
            ),
            BusinessRule(
                title="כוננות פעם אחת ליום לסוג דירה",
                summary="כוננות משולמת פעם אחת ביום לכל סוג דירה, גם אם יש כמה מקטעי כוננות.",
                details=(
                    "המפתח למניעת כפילות הוא סוג הדירה.",
                    "מקטעי כוננות רציפים/חופפים מתמזגים לפני בדיקת הביטול.",
                ),
                source=("app_utils.py:get_daily_segments_data",),
                tags=("כוננות", "כפילות"),
            ),
            BusinessRule(
                title="ביטול כוננות בגלל עבודה חופפת",
                summary=f"כאשר עבודה חופפת לפחות {int(STANDBY_CANCEL_OVERLAP_THRESHOLD * 100)}% מזמן הכוננות, הכוננות מתבטלת.",
                details=(
                    f"אם תעריף הכוננות גבוה מ-{MAX_CANCELLED_STANDBY_DEDUCTION:.0f} ש\"ח, משולם רק ההפרש.",
                    "אם החפיפה קטנה מהסף, הכוננות נחתכת ומשולמת רק על החלקים שלא חפפו עבודה.",
                    "חריג היסטורי 11-12/2025: חפיפה עם משמרת שמירה על דייר בלילה מבטלת גם את ההפרש, למעט שישי/שבת/חג.",
                ),
                source=("app_utils.py:get_daily_segments_data", "core/constants.py"),
                tags=("כוננות", "חפיפה", "קיזוז"),
            ),
            BusinessRule(
                title="יציאה מוקדמת מתוך כוננות",
                summary="כוננות שהסתיימה לפני זמן הסיום המוגדר וללא עבודה חופפת הופכת לשעות עבודה.",
                details=(
                    "הכלל מיועד למקרה שהמדריך יצא לפני סוף הכוננות ולא אמור לקבל כוננות מלאה.",
                    "שעות אלה נכנסות לרצף העבודה וממשיכות את השעות הנוספות.",
                ),
                source=("app_utils.py:get_daily_segments_data",),
                tags=("כוננות", "יציאה מוקדמת"),
            ),
            BusinessRule(
                title="כוננות בימי פרימיום",
                summary="יום פרימיום יכול להורות שכוננות תחושב בתעריף שבת במקום בתעריף רגיל.",
                details=(
                    "המדיניות נשמרת בשדה standby_mode.",
                    "פורים היסטורית משתמש בכוננות שבת; יום העצמאות הנוכחי הוגדר ככוננות רגילה.",
                    "סינון עיר של יום פרימיום חל גם על מדיניות הכוננות.",
                ),
                source=("core/premium_windows.py", "app_utils.py:_get_premium_standby_rate"),
                tags=("ימי פרימיום", "כוננות"),
                status="מנוהל במסך ימי פרימיום",
            ),
        ),
    ),
    BusinessRuleSection(
        key="absence",
        title="חופשה ומחלה",
        description="חישוב שעות ותשלום עבור היעדרויות.",
        rules=(
            BusinessRule(
                title="רצף ימי מחלה",
                summary="מחלה משולמת לפי מספר יום המחלה ברצף.",
                details=(
                    "יום ראשון: 0%.",
                    "ימים 2-3: 50%.",
                    "יום 4 והלאה: 100%.",
                    "ימי מחלה מהחודש הקודם נטענים כדי לשמור רצף בין חודשים.",
                    "רק תאריכי מחלה רצופים ממשיכים רצף; פער של יותר מיום אחד מאפס את הספירה.",
                ),
                source=("core/sick_days.py", "app_utils.py:_fetch_prev_month_sick_dates"),
                tags=("מחלה", "רצף"),
            ),
            BusinessRule(
                title="חופשה ומחלה ללא שעות",
                summary="דיווח חופשה/מחלה ללא שעות מקבל שעות לפי מקטעי המשמרת או לפי override של משמרת חול.",
                details=(
                    "מ-02/2026, אם יש override שעות חול לדירה/מערך, הוא משמש לחופשה/מחלה.",
                    "דריסת שעות נקראת לפי חודש החישוב מתוך shift_time_overrides_history, אם קיימת היסטוריה.",
                    "אם אין override, משתמשים במקטעי shift_time_segments של סוג המשמרת.",
                    "חופשה/מחלה נספרות כימי עבודה בפועל לצבירות.",
                ),
                source=("app_utils.py:get_daily_segments_data", "app_utils.py:_fetch_weekday_overrides_for_month", "app_utils.py:_build_weekday_work_overrides"),
                tags=("חופשה", "מחלה", "שעות חול"),
                effective_from="02/2026",
            ),
            BusinessRule(
                title="ברירת מחדל חופשה ומחלה",
                summary="בדירות רגילות, חופשה ומחלה משולמות לפי שכר מינימום ב-100%, ומחלה מוכפלת באחוז המחלה.",
                details=(
                    "חופשה נצברת כ-vacation_minutes ו-vacation_payment.",
                    "מחלה נצברת כ-sick_minutes, sick_payment, effective_sick_minutes ו-non_effective_sick_minutes.",
                    "פער ידוע: במסלול יום מעורב שיש בו גם עבודה וגם חופשה/מחלה, ההיעדרות מחושבת לפי שכר מינימום ומחלה אינה מקבלת את הדירוג המיוחד של מסלול יום היעדרות מלא.",
                ),
                source=("app_utils.py:get_daily_segments_data", "app_utils.py:aggregate_daily_segments_to_monthly"),
                tags=("חופשה", "מחלה", "100%"),
            ),
            BusinessRule(
                title="כלניות",
                summary="בדירת כלניות חג/חופשה/מחלה מחושבים לפי מבנה משמרת חול, עם כוננות ככוננות ולא כשעות עבודה.",
                details=(
                    "מקטעי עבודה מקבלים תעריף משמרת חול.",
                    f"מקטעי כוננות משלמים רק מעבר לקיזוז {MAX_CANCELLED_STANDBY_DEDUCTION:.0f} ש\"ח.",
                    "מ-02/2026 משמרת חול יכולה להגיע מ-override לפי דירה או מערך.",
                    "אם קיימת היסטוריית דריסות, משתמשים בערך שהיה תקף בחודש המחושב.",
                ),
                source=("app_utils.py:_calculate_special_absence_segment_payment", "core/holiday_payment.py:_calculate_special_holiday_day_pay"),
                tags=("כלניות", "חופשה", "מחלה", "חג"),
            ),
            BusinessRule(
                title="בראשית",
                summary="בדירת בראשית חג/חופשה/מחלה מחושבים לפי מבנה משמרת לילה, עם כוננות ככוננות ולא כשעות עבודה.",
                details=(
                    "מקטעי עבודה משולמים לפי תעריף משמרת לילה.",
                    f"מקטעי כוננות משלמים רק מעבר לקיזוז {MAX_CANCELLED_STANDBY_DEDUCTION:.0f} ש\"ח.",
                    "התוצאה יכולה להיות נמוכה ממשמרת מלאה כי כוננות אינה הופכת לשעות עבודה.",
                ),
                source=("app_utils.py:_calculate_special_absence_segment_payment", "core/holiday_payment.py:_calculate_special_holiday_day_pay"),
                tags=("בראשית", "חופשה", "מחלה", "חג"),
            ),
            BusinessRule(
                title="צבירות חופשה ומחלה",
                summary="צבירת ימי חופשה ומחלה מחושבת לפי ימי עבודה בפועל ותאריך תחילת עבודה.",
                details=(
                    "ימי עבודה בפועל כוללים עבודה, חופשה ומחלה.",
                    "הפירוט נשמר בשדות vacation_details, sick_days_accrued ו-vacation_days_accrued.",
                    "פער תצוגה ידוע: שורת חופשה בדוח הרצפים מחשבת לעיתים דקות כפול שכר מינימום במקום להציג ישירות את vacation_payment המחושב.",
                ),
                source=("app_utils.py:aggregate_daily_segments_to_monthly", "utils/utils.py:calculate_accruals"),
                tags=("צבירה", "חופשה", "מחלה"),
            ),
        ),
    ),
    BusinessRuleSection(
        key="holiday-payment",
        title="תשלום חג",
        description="רכיב 254 למדריכים קבועים לפי דירה וחודש.",
        rules=(
            BusinessRule(
                title="זכאות בסיסית",
                summary="תשלום חג ניתן רק למדריך קבוע שמשויך לדירה אמיתית ולא עבד ביום/חלון החג.",
                details=(
                    "מדריך מחליף אינו מקבל רכיב תשלום חג.",
                    "דירת השלמות אינה מקבלת תשלום חג, כי היא משמשת לדיווחי השלמות מחודשים קודמים ולא כדירה לחג.",
                    "אם המדריך עבד בדירה ביום החג, או בחלון חג מיוחד כמו יום העצמאות, התשלום לאותו חג מתבטל.",
                    "אם אין חגים בחודש, לא מחושב רכיב חג.",
                ),
                source=("core/holiday_payment.py:calculate_holiday_payments",),
                tags=("254", "חג", "קבועים"),
            ),
            BusinessRule(
                title="ותק מינימלי",
                summary=f"נדרש ותק של {HOLIDAY_PAY_MIN_SENIORITY_MONTHS} חודשים לפחות בתחילת חודש הדיווח.",
                details=(
                    "הבדיקה חלה על כל מערכי הדיור.",
                    "מדריך בלי ותק מספיק לא מקבל תשלום, גם אם נבחר בניהול תשלום חג.",
                    "אם בדירה יש שני מדריכים ואחד לא זכאי בגלל ותק, השני עדיין מחושב לפי מספר המשבצות בדירה.",
                ),
                source=("core/holiday_payment.py:_has_sufficient_seniority",),
                tags=("ותק", "254"),
            ),
            BusinessRule(
                title="ניהול תשלום חג כמקור קובע",
                summary="שורת ניהול תשלום חג שמורה היא המקור הקובע לדירה שלה בלבד.",
                details=(
                    "דירה עם שני שדות מדריכים ריקים פירושה שאין תשלום חג לדירה.",
                    "דירה שיש לה שורה שמורה משתמשת בבחירת המדריכים שבטבלה.",
                    "דירה שאין לה שורה שמורה ממשיכה להיבנות אוטומטית לפי דיווחים.",
                    "בכניסה למערכת יכול לקפוץ דיאלוג ניהול בחודש שיש בו חג ועדיין אין טבלה שמורה מלאה.",
                ),
                source=("core/holiday_payment.py:_load_saved_assignments", "core/holiday_payment.py:calculate_holiday_payments"),
                tags=("ניהול חג", "דיאלוג"),
                status="מנוהל במסך ניהול תשלום חג",
            ),
            BusinessRule(
                title="הצעות ושמירת ניהול חג",
                summary="הצעות המדריכים בדיאלוג נבנות לפי ימי עבודה בדירה, ושמירה עוברת ולידציה לפני כתיבה.",
                details=(
                    "הצעות לא כוללות דיווחי חופשה/מחלה.",
                    "דירות השלמות לא מוצגות בניהול תשלום חג.",
                    "הצעות ממוינות לפי מספר ימי עבודה, מספר משמרות ושם.",
                    "מדריך שני מוצע אוטומטית רק אם יש לו לפחות 7 ימי עבודה בחודש.",
                    "מדריך שלישי מוצג ונשמר רק לדירות ASD.",
                    "בשמירה נבחרים רק מדריכים קבועים פעילים במערך המותר; בחירה כפולה נדחית; מדריכים מאוחרים נמחקים אם חסר מדריך קודם.",
                ),
                source=("core/holiday_payment.py:_load_holiday_payment_suggestions", "core/holiday_payment.py:save_holiday_payment_setup"),
                tags=("ניהול חג", "הצעות", "שמירה"),
                status="מנוהל במסך ניהול תשלום חג",
            ),
            BusinessRule(
                title="מדריך אחד, שני מדריכים ומשבצת ללא תשלום",
                summary="מדריך אחד מקבל חג שלם; שני מדריכים מקבלים חצי; משבצת 'מדריך ללא תשלום חג' מחלקת את התשלום לחצי.",
                details=(
                    "אם נבחר מדריך שני אמיתי, כל מדריך זכאי מקבל חצי חג.",
                    "אם נבחרה משבצת 'מדריך ללא תשלום חג', היא נספרת רק לצורך חצי תשלום למדריך הראשון.",
                    "המשבצת אינה יוצרת תשלום לעובד נוסף.",
                ),
                source=("core/holiday_payment.py:calculate_holiday_payments", "templates/guide.html", "templates/index.html"),
                tags=("חצי חג", "ניהול חג"),
            ),
            BusinessRule(
                title="חריג ASD",
                summary="במערך דיור ASD אפשר לשייך עד שלושה מדריכים, וכל מדריך זכאי מקבל חג שלם.",
                details=(
                    "כלל החצי אינו חל על ASD.",
                    "מסך ניהול תשלום חג מציג מדריך שלישי רק לדירות ASD.",
                    "כלל הוותק עדיין חל גם ב-ASD.",
                    "מדריך שעבד בחג עדיין לא מקבל את אותו חג.",
                ),
                source=("core/holiday_payment.py:calculate_holiday_payments", "core/constants.py:is_asd_housing_array"),
                tags=("ASD", "254"),
            ),
            BusinessRule(
                title="חישוב סכום חג רגיל",
                summary="בדירה רגילה, חג שלם מחושב לפי דקות העבודה של הדירה כפול שכר מינימום; חצי חג לפי חצי דקות.",
                details=(
                    "דקות עבודה מגיעות מ-override של הדירה/מערך או מברירת מחדל של 480 דקות.",
                    "אם קיימת היסטוריית דריסות, משתמשים בערך שהיה תקף בחודש המחושב.",
                    "הסכום מחושב בשעות מעוגלות לשתי ספרות כפול שכר מינימום מעוגל.",
                ),
                source=("core/holiday_payment.py:_get_apartment_work_minutes", "core/holiday_payment.py:calculate_holiday_payments"),
                tags=("254", "סכום"),
            ),
            BusinessRule(
                title="חישוב חג בכלניות ובראשית",
                summary="בדירות כלניות ובראשית תשלום חג משתמש באותה לוגיקה מיוחדת של חופשה/מחלה לסוג הדירה.",
                details=(
                    "כלניות: לפי משמרת חול והקיזוז על כוננות.",
                    "בראשית: לפי משמרת לילה והקיזוז על כוננות.",
                    "בכלניות, דריסת שעות משמרת חול נקראת לפי חודש החישוב.",
                    "אם יש שני מדריכים, הסכום המיוחד נחצה, אלא אם מדובר ב-ASD.",
                ),
                source=("core/holiday_payment.py:_calculate_special_holiday_day_pay", "app_utils.py:_calculate_special_absence_segment_payment"),
                tags=("כלניות", "בראשית", "254"),
            ),
            BusinessRule(
                title="חגים מטבלת שבתות וחגים",
                summary="חג רגיל מזוהה לפי shabbat_times כאשר לשדה holiday יש ערך.",
                details=(
                    "שבת חול המועד מוחרגת מתשלום חג.",
                    "חג דו-יומי חייב רשומה ישירה לכל יום חג.",
                ),
                source=("core/holiday_payment.py:get_holiday_dates_in_month", "core/time_utils.py:get_shabbat_times_cache"),
                tags=("shabbat_times", "חגים"),
            ),
            BusinessRule(
                title="יום העצמאות וימי חג מיוחדים",
                summary="יום מיוחד נספר לתשלום חג רק אם הוא מסומן ב-special_days כ-counts_as_holiday_payment.",
                details=(
                    "החלון נשאר ב-special_days ולא מוכנס ל-shabbat_times.",
                    "בחלון שחוצה חצות, תאריך הזכאות הוא end_date.",
                    "ביטול התשלום נבדק לפי חפיפה שעותית לחלון המיוחד, לא רק לפי תאריך הדיווח.",
                    "משמרת מהיום שלפני החג שנמשכת לתוך החג לא מבטלת אם היא לא עברה את סוף המקטעים המוגדרים של המשמרת.",
                    "הדגל counts_as_holiday_payment נקבע בעת יצירת יום פרימיום; במסך הנוכחי אין פעולת עריכה ייעודית לדגל אחרי יצירה.",
                ),
                source=("core/holiday_payment.py:_get_special_holiday_payment_window_details", "core/holiday_payment.py:_report_overlaps_special_holiday_window"),
                tags=("יום העצמאות", "ימי פרימיום", "254"),
                status="מנוהל במסך ימי פרימיום",
            ),
        ),
    ),
    BusinessRuleSection(
        key="tagbur",
        title="משמרות תגבור",
        description="כללי תגבור לפי זמני שבת וחג.",
        rules=(
            BusinessRule(
                title="תגבור שישי / ערב חג",
                summary=f"תגבור שישי או ערב חג אמור להתחיל {TAGBUR_FRIDAY_PRE_ENTRY_MINUTES} דקות לפני כניסת שבת/חג.",
                details=(
                    "המקטע הראשון נבנה דינמית לפי shabbat_times.",
                    "מ-04/2026 אם הדיווח התחיל אחרי תחילת המקטע הדינמי, התשלום מתחיל לפי שעת הדיווח.",
                    "מ-12/2025 שעות לא מכוסות אחרי תגבור ערב אינן מתווספות כשעות עבודה.",
                    "לפני 12/2025 רק ההחלטה אילו שעות לא מכוסות לדלג נקבעת לפי יום בשבוע; גבולות תגבור עדיין מגיעים מזמני שבת/חג.",
                ),
                source=("core/constants.py", "app_utils.py:get_daily_segments_data"),
                tags=("תגבור", "שישי", "ערב חג"),
            ),
            BusinessRule(
                title="תגבור שבת / חג",
                summary=f"תגבור שבת או חג אמור להסתיים {TAGBUR_SHABBAT_POST_EXIT_MINUTES} דקות אחרי צאת שבת/חג.",
                details=(
                    "המקטע האחרון נבנה דינמית לפי זמן יציאה.",
                    "מ-04/2026 אם הדיווח הסתיים לפני סוף המקטע הדינמי, התשלום מסתיים לפי שעת הדיווח.",
                    "מ-12/2025 שעות לא מכוסות לפני תגבור שבת/חג אינן מתווספות כשעות עבודה.",
                    "שעות אחרי המקטע יכולות להיכנס כשעות לא מכוסות לפי הדיווח.",
                ),
                source=("core/constants.py", "app_utils.py:get_daily_segments_data"),
                tags=("תגבור", "שבת", "חג"),
            ),
            BusinessRule(
                title="תגבור כחלק מהרצף",
                summary="משמרות תגבור אינן מחושבות כיום קבוע נפרד; הן נכנסות לרצפי העבודה הרגילים ולשעות נוספות.",
                details=(
                    "הדוח מסמן את סוג המשמרת כ'תגבור'.",
                    "הפיצול לשבת/חול נעשה לפי זמן בפועל בתוך גבולות שבת/חג.",
                    "במסלול השכר, מקטעי התגבור המוגדרים מתווספים כמקטעים קבועים ולא נחתכים לפי חפיפת שעות הדיווח; רק שעות לא מכוסות לפני/אחרי מטופלות לפי הכללים.",
                    "דוח המשמרות ו-PDF המשמרות משתמשים באותו helper של גבולות תגבור כדי להציג את שעות התגבור לפי כלל החודש.",
                ),
                source=("app_utils.py:get_daily_segments_data", "core/shift_hours.py:apply_tagbur_dynamic_boundaries"),
                tags=("תגבור", "רצפים"),
            ),
            BusinessRule(
                title="תגבור משתמע בדירה טיפולית",
                summary="משמרת שישי/שבת בדירה טיפולית עם תעריף דירה רגילה מוצגת ומחושבת כתגבור.",
                details=(
                    "הכלל מזהה מצב שבו סוג הדירה בפועל טיפולי אבל סוג הדירה לתשלום רגיל.",
                    "הזיהוי משפיע על תווית הדוח ועל כללי תגבור.",
                ),
                source=("core/constants.py:is_implicit_tagbur", "app_utils.py:get_daily_segments_data"),
                tags=("תגבור", "דירה טיפולית"),
            ),
        ),
    ),
    BusinessRuleSection(
        key="premium-days",
        title="ימי פרימיום",
        description="ימים מיוחדים שמשפיעים על תעריף עבודה בפועל.",
        rules=(
            BusinessRule(
                title="מקור ימי פרימיום",
                summary="פורים, יום העצמאות, בחירות וימים מותאמים נשמרים בטבלת special_days.",
                details=(
                    "יום פרימיום פעיל רק כאשר is_active=true.",
                    "אפשר להגדיר חלון תאריכים ושעות, אחוז שכר, מדיניות כוננות, סינון ערים והאם נספר לתשלום חג.",
                    "שבתות וחגים רגילים אינם מגיעים מ-special_days אלא מ-shabbat_times.",
                ),
                source=("core/premium_windows.py", "routes/admin.py:manage_special_days"),
                tags=("פורים", "יום העצמאות", "בחירות"),
                status="מנוהל במסך ימי פרימיום",
            ),
            BusinessRule(
                title="עבודה בתוך חלון פרימיום",
                summary="רק דקות עבודה בפועל בתוך חלון הפרימיום מקבלות את אחוז הפרימיום.",
                details=(
                    "חלון 150% מתנהג כמו שבת מבחינת מדרגות: 150%, 175%, 200%.",
                    "חלון 200% ומעלה מתנהג כתעריף שטוח באחוז החלון.",
                    "חלון יכול להיחתך לפי עיר הדירה.",
                    "פער ידוע: ברצף שמערב כמה דירות, סינון העיר נעשה לפי עיר הסגמנט הראשון של הרצף.",
                ),
                source=("app_utils.py:_calculate_chain_wages", "core/premium_windows.py:filter_windows_by_city"),
                tags=("ימי פרימיום", "150%", "200%"),
            ),
            BusinessRule(
                title="יום בחירות",
                summary="המערכת תומכת ביום בחירות כחלון פרימיום, בדרך כלל 200%, אך אין כרגע כלל אוטומטי לשבתון למי שלא עבד.",
                details=(
                    "תשלום למי שעבד ביום בחירות יוגדר ב-special_days.",
                    "תשלום שבתון למי שלא עבד דורש כלל נפרד ואינו חלק מלוגיקת תשלום חג הנוכחית.",
                ),
                source=("core/premium_windows.py", "app_utils.py:_calculate_chain_wages"),
                tags=("בחירות", "200%"),
                status="נתמך כפרימיום; שבתון דורש כלל נוסף",
            ),
        ),
    ),
    BusinessRuleSection(
        key="extras-export",
        title="רכיבים וייצוא",
        description="נסיעות, תוספות, תומך מקצועי וייצוא גשר.",
        rules=(
            BusinessRule(
                title="רכיבי תשלום ידניים",
                summary="payment_components נצברים לפי quantity*rate ומסווגים לפי סוג הרכיב.",
                details=(
                    "סוגים 2 ו-7 נצברים כנסיעות.",
                    "סוג 13 נצבר כתומך מקצועי.",
                    "רכיב שמסומן for_pension נכנס לתוספות לפנסיה; השאר לתוספות רגילות.",
                    "הסיווג נשמר גם במסלול עובד יחיד וגם במסלול הסיכום החודשי המרוכז.",
                ),
                source=("app_utils.py:aggregate_daily_segments_to_monthly", "core/logic.py:calculate_monthly_summary"),
                tags=("נסיעות", "תוספות", "תומך מקצועי"),
            ),
            BusinessRule(
                title="תצוגת רכיבי תשלום בדוחות",
                summary="בדוחות PDF/משמרות רכיבי תשלום מקובצים ומוצגים לפי סוג ותיאור.",
                details=(
                    "נסיעות ותומך מקצועי מקובצים לפי סוג והרכיב מוצג ללא פירוט תיאור.",
                    "רכיבים אחרים מוצגים עם התיאור אם קיים.",
                    "רכיבים שמשויכים לדירת השלמות מופרדים לנתוני השלמות.",
                ),
                source=("routes/guide.py:prepare_guide_pdf_data",),
                tags=("דוחות", "רכיבים", "השלמות"),
            ),
            BusinessRule(
                title="החרגת השלמות ASD באפריל 2026",
                summary="בחודש 04/2026 בלבד, דיווחי משמרות דירת השלמות במערך ASD אינם נכנסים לשכר או לגשר.",
                details=(
                    "ההחרגה חלה רק על time_reports בדירות השלמות ורק כאשר מערך הדיור הוא ASD.",
                    "מערכי דיור אחרים וחודשים אחרים לא מושפעים.",
                    "המטרה היא טיפול חד-פעמי בדיווחי השלמות מחודשים קודמים בדרך אחרת.",
                ),
                source=("core/constants.py:should_exclude_asd_completion_report", "app_utils.py:get_daily_segments_data", "core/logic.py:calculate_monthly_summary"),
                tags=("ASD", "השלמות", "גשר", "חד פעמי"),
                status="חריג חד-פעמי ל-04/2026",
            ),
            BusinessRule(
                title="אישור אוטומטי נסיעות מחליף",
                summary="נסיעות מדריך מחליף מאושרות אוטומטית כאשר כל דיווחי אותו יום לאותו מדריך מאושרים.",
                details=(
                    "הכלל חל על component_type_id של נסיעות מדריך מחליף.",
                    "האישור מתבצע לפי תאריך רכיב התשלום ולפי דיווחי time_reports באותו תאריך.",
                ),
                source=("core/logic.py:auto_approve_substitute_travel", "routes/guide.py"),
                tags=("נסיעות", "מחליף", "אישור"),
            ),
            BusinessRule(
                title="ימי עבודה בפועל",
                summary="ימי עבודה בפועל הם איחוד של ימי עבודה, חופשה ומחלה.",
                details=(
                    "כוננות לבדה אינה מוסיפה יום עבודה בפועל.",
                    "הנתון משמש גם לייצוא וגם לצבירות.",
                ),
                source=("app_utils.py:aggregate_daily_segments_to_monthly", "services/gesher_exporter.py:calculate_value"),
                tags=("ימי עבודה", "צבירה"),
            ),
            BusinessRule(
                title="מקור קודי גשר",
                summary="קודי הייצוא נטענים מטבלת payment_codes לפי internal_key ו-merav_code.",
                details=(
                    "רכיבים ללא קוד מירב אינם מיוצאים.",
                    "אם אין תצורה ב-DB, יש fallback לקובץ INI ישן.",
                    "סוג הערך נקבע לפי מיפוי פנימי: שעות, כסף, ימים, כוננות, מחלה ותעריף משתנה.",
                ),
                source=("services/gesher_exporter.py:load_export_config_from_db", "routes/admin.py:manage_payment_codes"),
                tags=("גשר", "מירב", "סמלי שכר"),
                status="מנוהל חלקית במסך סמלי שכר",
            ),
            BusinessRule(
                title="קודים מוחרגים מייצוא",
                summary="קודי מירב 130 ו-199 אינם נכתבים לקובץ גשר גם אם הם קיימים בתצוגה.",
                details=(
                    "ההחרגה חלה גם על ייצוא עובד יחיד, גם על ייצוא כללי וגם על ייצוא מרובה.",
                ),
                source=("services/gesher_exporter.py:EXCLUDED_EXPORT_CODES",),
                tags=("גשר", "130", "199"),
            ),
            BusinessRule(
                title="פורמט שורת גשר",
                summary="כל שורת גשר כוללת מספר עובד, סמל, כמות, תעריף וסיומת 201.",
                details=(
                    "הכותרת כוללת קוד מפעל, שנה דו-ספרתית וחודש.",
                    "הקובץ נכתב עם CRLF.",
                    "ערכי אפס מדולגים כברירת מחדל לפי min_amount.",
                    "נתיב הייצוא המלא דורש company מפורש בבקשה; ברירת המחדל של exporter אינה מופעלת דרך route זה.",
                    "ייצוא מרובה מדריכים יוצר קובץ אחד עם כותרת לפי החברה של המדריך הראשון שנבחר.",
                ),
                source=("services/gesher_exporter.py:format_gesher_header", "services/gesher_exporter.py:format_gesher_line", "routes/export.py"),
                tags=("גשר", "פורמט"),
            ),
            BusinessRule(
                title="כללי עמוד דוחות ושליחה",
                summary="עמוד הדוחות כולל רק מדריכים רלוונטיים לחודש, והפקת PDF/מייל משתמשת בתבניות HTML.",
                details=(
                    "ברשימת הדוחות מופיעים רק מדריכים מסוג קבוע/מחליף שיש להם לפחות משמרת או רכיב תשלום בחודש.",
                    "מנהל מערך מוגבל למערך שלו ברשימת הדוחות.",
                    "PDF מופק מתבנית HTML באמצעות Edge/Chrome headless ללא header/footer של הדפדפן.",
                    "שליחת מייל מצרפת PDF, משתמשת ב-HTML RTL ובכותרות UTF-8.",
                ),
                source=("routes/reports.py:reports_page", "routes/guide.py:_generate_shifts_pdf", "routes/guide.py:_generate_chains_pdf", "services/email_service.py"),
                tags=("דוחות", "PDF", "מייל"),
            ),
        ),
    ),
)


def get_business_rule_sections() -> tuple[BusinessRuleSection, ...]:
    return BUSINESS_RULE_SECTIONS


def get_business_rule_reference_tables() -> tuple[BusinessRuleTable, ...]:
    return BUSINESS_RULE_TABLES
