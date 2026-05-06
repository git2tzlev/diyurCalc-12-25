"""
קטלוג כללים עסקיים גלוי למערכת.

המטרה היא לתעד את הכללים כפי שהם ממומשים בקוד בפועל, בצורה שאפשר להציג
בממשק ולתחזק ליד שינויי קוד. זה אינו מקור החישוב עצמו.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.constants import (
    ASD_NIGHT_STANDBY_RATE,
    ASD_SENIORITY_SUPPLEMENT,
    ASD_SENIORITY_YEARS_THRESHOLD,
    BREAK_THRESHOLD_MINUTES,
    DEFAULT_STANDBY_RATE,
    HOLIDAY_PAY_MIN_SENIORITY_MONTHS,
    MAX_CANCELLED_STANDBY_DEDUCTION,
    MINIMUM_ESCORT_MINUTES,
    NIGHT_HOURS_THRESHOLD,
    NIGHT_SHIFT_WORK_FIRST_MINUTES,
    STANDBY_CANCEL_OVERLAP_THRESHOLD,
    TAGBUR_FRIDAY_PRE_ENTRY_MINUTES,
    TAGBUR_SHABBAT_POST_EXIT_MINUTES,
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
                ),
                source=("core/auth.py:get_user_housing_array", "core/auth.py:enforce_framework_manager_guide_access"),
                tags=("הרשאות", "מנהל מערך"),
                status="הרשאה",
            ),
            BusinessRule(
                title="נעילת חודש",
                summary="חודש נעול מונע עדכון הגדרות רגישות לחודש הנוכחי עד לפתיחה מחדש.",
                details=(
                    "נעילה נשמרת בטבלת month_locks.",
                    "אם unlocked_at מלא, החודש נחשב פתוח.",
                    "עדכוני תעריפי משמרות ומקטעי משמרות בודקים נעילה לפני שינוי.",
                ),
                source=("core/history.py:is_month_locked", "routes/admin.py:update_shift_type_rate", "routes/admin.py:update_shift_segment"),
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
                summary="ב-ASD תפקוד גבוה משאיר כוננות ככוננות לילה, ותפקוד נמוך הופך כוננות לשעות עבודה.",
                details=(
                    f"תפקוד גבוה מקבל כוננות לילה בסך {ASD_NIGHT_STANDBY_RATE:.0f} ש\"ח.",
                    "תפקוד נמוך: מקטע standby בלילה הופך ל-work.",
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
                summary="כוננות שוברת רצף רק כאשר אין עבודה שחופפת לה.",
                details=(
                    "עבודה חופפת לכוננות מאפשרת לרצף העבודה להימשך.",
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
                title="רצף מחודש קודם",
                summary="אם היום האחרון של החודש הקודם ממשיך לרצף שמסתיים ב-08:00, offset הרצף עובר לחודש הנוכחי.",
                details=(
                    "המערכת מחפשת אחורה עד 31 ימים או עד יום ללא דיווחים.",
                    "חופשה/מחלה ללא שעות שוברת carryover מהחודש הקודם.",
                    "הcarryover כולל גם דקות לילה לצורך סף משמרת לילה.",
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
                ),
                source=("core/sick_days.py", "app_utils.py:_fetch_prev_month_sick_dates"),
                tags=("מחלה", "רצף"),
            ),
            BusinessRule(
                title="חופשה ומחלה ללא שעות",
                summary="דיווח חופשה/מחלה ללא שעות מקבל שעות לפי מקטעי המשמרת או לפי override של משמרת חול.",
                details=(
                    "מ-02/2026, אם יש override שעות חול לדירה/מערך, הוא משמש לחופשה/מחלה.",
                    "אם אין override, משתמשים במקטעי shift_time_segments של סוג המשמרת.",
                    "חופשה/מחלה נספרות כימי עבודה בפועל לצבירות.",
                ),
                source=("app_utils.py:get_daily_segments_data", "app_utils.py:_build_weekday_work_overrides"),
                tags=("חופשה", "מחלה", "שעות חול"),
                effective_from="02/2026",
            ),
            BusinessRule(
                title="ברירת מחדל חופשה ומחלה",
                summary="בדירות רגילות, חופשה ומחלה משולמות לפי שכר מינימום ב-100%, ומחלה מוכפלת באחוז המחלה.",
                details=(
                    "חופשה נצברת כ-vacation_minutes ו-vacation_payment.",
                    "מחלה נצברת כ-sick_minutes, sick_payment, effective_sick_minutes ו-non_effective_sick_minutes.",
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
                summary="תשלום חג ניתן רק למדריך קבוע שמשויך לדירה ולא עבד ביום/חלון החג.",
                details=(
                    "מדריך מחליף אינו מקבל רכיב תשלום חג.",
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
                summary="אם נשמרה טבלת ניהול מלאה לחודש, הבחירות שם מחליפות את הזיהוי האוטומטי לפי דיווחים.",
                details=(
                    "דירה עם שני שדות מדריכים ריקים פירושה שאין תשלום חג לדירה.",
                    "אם הטבלה השמורה לא כוללת את כל הדירות הרלוונטיות, המערכת חוזרת לזיהוי לפי דיווחים.",
                    "בכניסה למערכת יכול לקפוץ דיאלוג ניהול בחודש שיש בו חג ועדיין אין טבלה שמורה מלאה.",
                ),
                source=("core/holiday_payment.py:_load_saved_assignments", "core/holiday_payment.py:calculate_holiday_payments"),
                tags=("ניהול חג", "דיאלוג"),
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
                summary="במערך דיור ASD כל מדריך זכאי מקבל חג שלם, גם כשיש יותר ממדריך אחד בדירה.",
                details=(
                    "כלל החצי אינו חל על ASD.",
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
                    "עבודה בכל אחד מתאריכי החלון מונעת תשלום חג לאותו יום.",
                ),
                source=("core/holiday_payment.py:_get_special_holiday_payment_windows", "core/holiday_payment.py:get_holiday_payment_dates_in_month"),
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
                    "מ-12/2025 שעות לא מכוסות אחרי תגבור ערב אינן מתווספות כשעות עבודה.",
                    "לפני 12/2025 נשמרה לוגיקה היסטורית לפי יום בשבוע.",
                ),
                source=("core/constants.py", "app_utils.py:get_daily_segments_data", "core/shift_hours.py:calculate_tagbur_segments"),
                tags=("תגבור", "שישי", "ערב חג"),
            ),
            BusinessRule(
                title="תגבור שבת / חג",
                summary=f"תגבור שבת או חג אמור להסתיים {TAGBUR_SHABBAT_POST_EXIT_MINUTES} דקות אחרי צאת שבת/חג.",
                details=(
                    "המקטע האחרון נבנה דינמית לפי זמן יציאה.",
                    "מ-12/2025 שעות לא מכוסות לפני תגבור שבת/חג אינן מתווספות כשעות עבודה.",
                    "שעות אחרי המקטע יכולות להיכנס כשעות לא מכוסות לפי הדיווח.",
                ),
                source=("core/constants.py", "app_utils.py:get_daily_segments_data", "core/shift_hours.py:calculate_tagbur_segments"),
                tags=("תגבור", "שבת", "חג"),
            ),
            BusinessRule(
                title="תגבור כחלק מהרצף",
                summary="משמרות תגבור אינן מחושבות כיום קבוע נפרד; הן נכנסות לרצפי העבודה הרגילים ולשעות נוספות.",
                details=(
                    "הדוח מסמן את סוג המשמרת כ'תגבור'.",
                    "הפיצול לשבת/חול נעשה לפי זמן בפועל בתוך גבולות שבת/חג.",
                ),
                source=("app_utils.py:get_daily_segments_data",),
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
                ),
                source=("app_utils.py:aggregate_daily_segments_to_monthly",),
                tags=("נסיעות", "תוספות", "תומך מקצועי"),
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
                ),
                source=("services/gesher_exporter.py:format_gesher_header", "services/gesher_exporter.py:format_gesher_line"),
                tags=("גשר", "פורמט"),
            ),
        ),
    ),
)


def get_business_rule_sections() -> tuple[BusinessRuleSection, ...]:
    return BUSINESS_RULE_SECTIONS
