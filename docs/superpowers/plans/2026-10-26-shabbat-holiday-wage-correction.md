# Shabbat And Holiday Wage Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** לתקן את חישוב השכר ותשלום החג כך ששבתות, חגים, רצפים וחלונות מיוחדים יחושבו לפי טווח זמן ממשי, ולמנוע הישנות באמצעות בדיקות רגרסיה ובקרת נתוני לוח.

**Architecture:** מקור האמת יהיה רשימת חלונות זמן רציפים מסוג `PremiumWindow`, ולא סיווג של תאריך שלם כ־`holy/eve/weekday`. טעינת `shabbat_times` תנורמל ותיבדק באופן דטרמיניסטי; מנוע השכר ותשלום החג ישתמשו באותו מנגנון חפיפה; כלי audit נפרד יחשב לפני/אחרי מול נתוני המערכת בלי לעדכן אותם.

**Tech Stack:** Python 3, FastAPI, psycopg2/PostgreSQL, pytest, Jinja2, שירותי PDF/מייל וייצוא גשר הקיימים.

**Spec:** בקשת המשתמשת בשיחה, ממצאי החקירה מ־26.10.2026 וכללי `AGENTS.md` שסופקו למשימה.

## Global Constraints

- אין לבצע `UPDATE`, `DELETE`, `ALTER`, תיקון תלושים או כתיבה אחרת בייצור בלי אישור מפורש נוסף.
- יום העבודה בדוחות נשאר 08:00–08:00, אך מצב שבת/חג נקבע לכל נקודת זמן לפי חלון מוחלט.
- חלון הוא חצי־פתוח: זמן כניסה נכלל, זמן יציאה אינו נכלל (`start <= t < end`).
- רצף צמוד של חג ושבת או שבת וחג חייב להיות רציף ללא דקת חול מלאכותית וללא כפל תשלום.
- `special_days` נשאר מקור האמת לפורים, יום העצמאות, בחירות וימים מותאמים; `shabbat_times` נשאר מקור האמת לשבתות ולחגים יהודיים.
- תשלום חג 254 ייחסם רק בשל דיווח עבודה שחופף בפועל לחלון החג. חופשה, מחלה, עבודה לפני הכניסה או אחרי היציאה אינן "עבודה בחג".
- חישוב חודש חייב לטעון גם דיווחים מערב החג בחודש הקודם כאשר החג חל ב־1 בחודש.
- PDF, מייל, הדפסה, סיכום חודשי וייצוא גשר יקבלו את אותם סכומים כמו מסך הדוח.
- יש לשמר ולבודד את השינויים הקיימים ב־`core/business_rules_catalog.py`, `core/history.py` ו־`tests/test_standby_rate_history.py` ולא לדרוס אותם.

## Production Impact Snapshot — Read Only

המספרים להלן חושבו מול נתוני המערכת בקריאה בלבד, ללא שינוי נתונים. הם ישמשו baseline לבדיקת מנוע התיקון.

| רכיב | מערך | מספר מדריכים | corrected - current | משמעות |
|---|---:|---:|---:|---|
| שכר עבודה, משמרות שנפגעו ב־20.09.2026 | צוהר הלב | 47 | ‎-3,618.00 ₪ | שולם ביתר כיום |
| שכר עבודה, משמרות שנפגעו ב־20.09.2026 | ASD | 12 | ‎-931.00 ₪ | שולם ביתר כיום |
| **סה״כ שכר עבודה מאומת** | **כל המערכים** | **59** | **‎-4,549.00 ₪** | **תשלום יתר נוכחי** |
| תשלום חג 254, סימולציית חפיפה | צוהר הלב, 04/2026 | 1 | ‎-132.75 ₪ | מועמד להפחתה |
| תשלום חג 254, סימולציית חפיפה | ASD, 04/2026 | 1 | ‎+265.50 ₪ | מועמד לתוספת |
| תשלום חג 254, סימולציית חפיפה | צוהר הלב, 09/2026 | 4 | ‎+831.90 ₪ | מועמד לתוספת |
| תשלום חג 254, סימולציית חפיפה | ASD, 09/2026 | 4 | ‎+1,292.10 ₪ | מועמד לתוספת |
| תשלום חג 254, סימולציית חפיפה | צוהר הלב, 10/2026 | 1 | ‎+415.95 ₪ | מועמד לתוספת |
| **סה״כ תשלום חג — אבחוני בלבד** | **כל המערכים** |  | **‎+2,672.70 ₪** | **דורש אימות במנוע המתוקן** |

- השפעת שכר העבודה בסך 4,549.00 ₪ מאומתת ברמת כל המשמרת, כולל שלוש משמרות שנדחו לתשלום באוקטובר.
- השפעת תשלום החג היא סימולציה אבחונית של הכלל החדש, ולכן אינה הוראת תיקון. לאחר המימוש יש להפיק ledger מדויק לפי מדריך, דירה, חג וסיבת הפרש.
- אם הסימולציה תאושר ללא שינוי, ההשפעה המשולבת היא `corrected - current = -1,876.30 ₪`; יש להשאיר את שני הרכיבים נפרדים בדוח ובכל פעולת תיקון עתידית.
- חלונות שגויים נוספים נמצאו ב־12.04.2025, 01.06.2025, 23.09.2025, 11.06.2027 ו־02.10.2027. בטווח דיווחי הייצור שנבדק לא נמצאו משמרות מושפעות בימים אלה; הם עדיין חייבים בדיקות רגרסיה.

## Review Focus

- יום ראשון שהוא ערב חג ביום שני: 00:00–כניסת החג הוא חול, והפרימיום מתחיל רק בכניסה.
- רצף שבת→חג, חג→שבת וחג דו־יומי: אין חור ואין שימוש בשעת ברירת מחדל כאשר קיים חלון מפורש.
- רשומות כפולות ב־`shabbat_times`: תוצאה דטרמיניסטית; רשומות `00:00/00:00` שאינן רצף חוקי אינן גוברות על שורה תקינה.
- ראש השנה: כל יום חג מקבל רשומה/חלון מפורש; אין להסיק שכניסה בשורה של היום השני שייכת אוטומטית לערב היום הראשון.
- דיווח חופשה או מחלה ביום חג אינו חוסם תשלום חג 254.
- משמרת שחוצה חודש, חצות או 08:00 משויכת נכון לתקופת העבודה ולתקופת התשלום.
- חלון `special_days` מסונן לפי עיר הדירה של כל מקטע ברצף, ולא לפי הדירה הראשונה בלבד.
- roundings, כוננות, שעות נוספות וייצוא גשר אינם משתנים מחוץ לדקות ששינו סיווג.

---

### Task 1: לנעול את התקלה בבדיקות כושלות

**Files:**
- Create: `tests/test_sacred_windows.py`
- Modify: `tests/test_salary_calculation.py`
- Modify: `tests/test_chain_wages_regression.py`

**Interfaces:**
- Consumes: `PremiumWindow`, `classify_day_type(...)`, `_calculate_chain_wages(...)`.
- Produces: מטריצת אמת של חלונות, גבולות ושכר לפני שינוי קוד הייצור.

- [ ] להוסיף fixture קטן של `shabbat_times` עם שבת רגילה, יום ראשון ערב חג ביום שני, חג צמוד לשבת, ראש השנה דו־יומי ו־`exit=00:00` שממשיך ליום הבא.
- [ ] להוסיף רגרסיה מדויקת ל־20.09.2026: 00:00–17:58 חול, 17:58–19:27 חג, ומ־19:27 ואילך חול.
- [ ] להוסיף רגרסיות ל־01.06.2025 ול־10.10.2027 כדי למנוע חזרה של אותה תקלה בשנים אחרות.
- [ ] להוסיף רגרסיות ל־12.04.2025, 23.09.2025, 11.06.2027 ו־02.10.2027, כולל הדקה שלפני/בדיוק/אחרי כל גבול.
- [ ] להוסיף invariant שעובר דקה־דקה בטווח fixture וקובע: 150% שבת/חג אם ורק אם ה־datetime נמצא בחלון המקודש.
- [ ] להריץ `py -m pytest tests\test_sacred_windows.py tests\test_salary_calculation.py tests\test_chain_wages_regression.py -q` ולוודא שהבדיקות החדשות נכשלות מהסיבות המתועדות.

### Task 2: לנרמל את נתוני שבת/חג ולבנות חלונות דטרמיניסטיים

**Files:**
- Modify: `core/premium_windows.py`
- Modify: `core/time_utils.py`
- Create: `scripts/audit_shabbat_times.py`
- Modify: `tests/test_premium_windows.py`
- Test: `tests/test_sacred_windows.py`

**Interfaces:**
- Consumes: שורות `shabbat_times` בטווח תאריכים.
- Produces: `load_sacred_windows(conn, start_date, end_date) -> list[PremiumWindow]` ורשימת חריגות נתונים מובנית.

- [ ] להוסיף בדיקות כושלות לנרמול תאריך: שורה יחידה, כפילויות זהות, placeholder של `00:00/00:00` לצד שורה תקינה, שתי שורות תקינות סותרות ושעה לא תקינה.
- [ ] להגדיר validator טהור שמסיר placeholders לא חוקיים, מאחד כפילויות זהות ומדווח על סתירה במקום לבחור לפי סדר החזרה מה־DB.
- [ ] לטעון עם `ORDER BY` מפורש ולבנות חלונות מוחלטים לפי `shabbat_date - 1 + candle_lighting` ועד `shabbat_date + havdalah`.
- [ ] לאחד חלונות חופפים/צמודים תוך שמירת origin ושמות החגים לצורכי תצוגה וביקורת.
- [ ] להסיר מהמסלול החדש את ההנחה שרשומת היום השני של ראש השנה מכילה את שעת הכניסה של היום הראשון; נתון חסר יופיע כחריגה ולא ינוחש.
- [ ] להשאיר את `classify_day_type` ו־`_get_shabbat_boundaries` כעטיפות תאימות זמניות בלבד, המבוססות על החלונות החדשים, ולסמן את אתרי השימוש שנותרו להסרה.
- [ ] לממש `scripts/audit_shabbat_times.py --start YYYY-MM-DD --end YYYY-MM-DD` בקריאה בלבד, עם ספירות של חסרים, כפילויות, placeholders, סתירות ורצפים חשודים; בלי להדפיס connection string.
- [ ] להריץ את בדיקות Task 1 ואת `tests/test_premium_windows.py` עד מעבר.

### Task 3: להעביר את מנוע שכר העבודה לחלונות מוחלטים

**Files:**
- Modify: `app_utils.py`
- Modify: `core/logic.py`
- Modify: `tests/test_salary_calculation.py`
- Modify: `tests/test_chain_wages_regression.py`
- Modify: `tests/test_logic.py`

**Interfaces:**
- Consumes: כל חלונות `shabbat`, `holiday` ו־`special_days` לטווח הרצף.
- Produces: פירוט שעות ושכר לפי 100/125/150/175/200 ללא offset המבוסס על סיווג תאריך שלם.

- [ ] להוסיף בדיקה כושלת לרצף מלא מ־08:00 עד 08:00 ביום ראשון ערב חג, כולל שעות נוספות לפני הכניסה, חג ואחרי היציאה.
- [ ] להסיר את הסינון שמוציא `shabbat`/`holiday` מ־`premium_windows_all`.
- [ ] לשנות את `_calculate_chain_wages` כך שכל בלוק ייחתך בגבולות החלונות המוחלטים; אין להשתמש ב־`+1440` עקב `day_type == "holy"`.
- [ ] לשמר את כללי שעות נוספות, משמרת לילה, כוננות, תגבור ועיגול הקיימים, ולבדוק שאין שינוי במשמרת חול רגילה.
- [ ] לסנן חלונות עירוניים לפי עיר הדירה של כל מקטע ברצף; להוסיף רגרסיה לרצף שעובר בין שתי דירות בערים שונות.
- [ ] להריץ `py -m pytest tests\test_salary_calculation.py tests\test_chain_wages_regression.py tests\test_premium_windows.py tests\test_logic.py -q`.

### Task 4: לתקן את קביעת "עבד בחג" בתשלום חג 254

**Files:**
- Modify: `core/holiday_payment.py`
- Modify: `tests/test_holiday_payment.py`

**Interfaces:**
- Consumes: חלון החג בפועל ודיווחי עבודה החל מערב היום הראשון בחודש.
- Produces: `report_overlaps_holiday_window(report, window) -> bool` עבור חג רגיל ומיוחד כאחד.

- [ ] להוסיף בדיקות כושלות: עבודה לפני כניסה בלבד; עבודה אחרי כניסה בערב החג; עבודה ביום החג; עבודה אחרי יציאה בלבד; משמרת שחוצה את הכניסה/היציאה.
- [ ] להוסיף בדיקות שחופשה, מחלה ודיווח ללא שעות עבודה אינם מסמנים את המדריך כמי שעבד בחג.
- [ ] להוסיף בדיקת חג ב־1 בחודש שעבודת ערב החג בחודש הקודם כן נטענת וחוסמת תשלום.
- [ ] להוסיף בדיקת ראש השנה שבה כל יום נבדק מול החלון המתאים ללא ספירה כפולה.
- [ ] להחליף את `report_date == holiday_date` ואת fallback של תאריכי עבודה בבדיקת חפיפה שעתית משותפת.
- [ ] להרחיב את `_load_reports_and_types` ואת מסלול החישוב הקבוצתי לטעינת טווח הזמן הנדרש, תוך שמירת `housing_filter` והיסטוריית סוג מדריך.
- [ ] לבדוק במפורש את כלל ASD לפני/אחרי מאי 2026 ואת `special_days` שסומן `counts_as_holiday_payment`.
- [ ] להריץ `py -m pytest tests\test_holiday_payment.py -q`.

### Task 5: להוסיף audit חוזר של הפרשי שכר על נתוני המערכת

**Files:**
- Create: `scripts/audit_holiday_shabbat_wage_impact.py`
- Create: `tests/test_holiday_shabbat_wage_impact.py`

**Interfaces:**
- Consumes: `--start`, `--end`, מערך אופציונלי ומצב `--read-only` שהוא ברירת המחדל.
- Produces: CSV/JSON עם `person_id`, שם, מערך, דירה, תקופת עבודה, תקופת תשלום, מזהי דיווחים, current, corrected, delta ו־reason.

- [ ] לכתוב בדיקה כושלת שמריצה את מנוע ה־audit על DB מזויף ומוודאת שכל משמרת מושפעת מופיעה פעם אחת בלבד, גם אם נדחתה לחודש הבא.
- [ ] להפריד בדוח בין `work_wage_delta` לבין `holiday_payment_delta`; לא לקזז ביניהם ברמת שורה.
- [ ] להוסיף totals לפי מדריך, מערך, חודש ורכיב שכר, וכן checksum של מזהי הדיווחים שנכללו.
- [ ] להבטיח שהסקריפט מבצע `SELECT` בלבד, אינו שומר תוצאה ב־DB ואינו חושף פרטי התחברות.
- [ ] להריץ בייצור בקריאה בלבד לטווח 01.09.2025–31.10.2026 ולהשוות ל־baseline: שכר עבודה ‎-4,549.00 ₪; הפרש תשלום חג אבחוני ‎+2,672.70 ₪.
- [ ] לעצור ולחקור כל סטייה מה־baseline לפני פעולת תיקון; ה־ledger החדש, ולא הסימולציה הישנה, יהיה המקור לסכומים הסופיים.
- [ ] לשמור artifact מקומי מחוץ ל־repo או להציג סיכום בלבד; אין להכניס שמות עובדים/נתוני שכר למסמכי Git.

### Task 6: לתקן ולאמת את איכות נתוני הלוח

**Files:**
- Modify: `scripts/audit_shabbat_times.py`
- Modify: `tests/test_sacred_windows.py`
- Optional after approval: production rows in `shabbat_times` (data change, no schema change).

**Interfaces:**
- Consumes: טווח לוח 2025–2028 ומקור זמנים מוסכם.
- Produces: טבלת חריגות ריקה וחלון מפורש לכל שבת/חג נדרש.

- [ ] להפיק לפני שינוי רשימה מלאה של 9 תאריכים כפולים, 20 placeholders, השבת החסרה 12.04.2025 ויום א' של ראש השנה 23.09.2025.
- [ ] לאמת כל ערך מתוקן מול מקור לוח מוסכם ולתעד מקור ותאריך בדיקה; אין להשתמש בזמן ברירת מחדל כתיקון נתונים.
- [ ] להכין transaction ממוקד עם `SELECT` לפני/אחרי וספירות, אך לא להריץ אותו בייצור ללא אישור מפורש.
- [ ] לאחר אישור בלבד, לתקן את השורות הנקודתיות ולרוקן את cache של `shabbat_times` באופן מבוקר.
- [ ] להריץ שוב את audit ולוודא שאין סתירות, חסרים או placeholders בלתי מוסברים בטווח.
- [ ] להוסיף בדיקת תחזוקה שנתית: טעינת השנה הבאה נכשלת ב־CI אם חסרים שבת/חג, קיימת סתירה או חלון אינו תקין.

### Task 7: לאמת את כל צרכני השכר והדוחות

**Files:**
- Verify/modify as needed: `routes/guide.py`
- Verify/modify as needed: `services/gesher_exporter.py`
- Verify/modify as needed: `services/email_service.py`
- Verify/modify as needed: תבניות PDF/הדפסה תחת `templates/`
- Modify: `core/business_rules_catalog.py`
- Test: `tests/test_gesher_export_preview.py`
- Test: `tests/test_pdf_renderer.py`
- Test: `tests/test_guide_reports_excel_export.py`
- Test: `tests/test_guide_payment_period_rows.py`

**Interfaces:**
- Consumes: תוצאות המנוע המתוקן.
- Produces: סכומים זהים במסך, PDF, מייל, Excel וגשר.

- [ ] להוסיף/לעדכן בדיקה אחת שמזרימה אותו מדריך ואותו חודש לכל מסלולי הפלט ומשווה שעות, תשלום חג וסכום כולל.
- [ ] לוודא שייצוא גשר מקבל את תקופת התשלום הנכונה למשמרות דחויות ושומר על כללי העיגול הקיימים.
- [ ] לוודא ש־PDF ומייל אינם מחשבים מחדש את החג בדרך אחרת.
- [ ] לעדכן את `core/business_rules_catalog.py` כך שיתאר חלונות מוחלטים, חפיפת עבודה בחג ובקרת נתונים; למזג בזהירות עם השינויים שכבר קיימים בקובץ.
- [ ] להריץ את כל בדיקות ה־routes/services הרלוונטיות.

### Task 8: אימות סופי ושער הפעלה

**Files:**
- Test: `tests/`
- Verify: `app.py`, `app_utils.py`, `core/`, `routes/`, `services/`

**Interfaces:**
- Consumes: כל השינויים במשימה.
- Produces: עדות מעבר בדיקות, audit ייצור לקריאה בלבד ותוכנית תיקון נתונים נפרדת לאישור.

- [ ] להריץ קומפילציה: `$files = @('app.py','app_utils.py') + (Get-ChildItem core,routes,services -Filter *.py | ForEach-Object { $_.FullName }); py -m py_compile @files`.
- [ ] להריץ בדיקות ממוקדות: `py -m pytest tests\test_sacred_windows.py tests\test_salary_calculation.py tests\test_chain_wages_regression.py tests\test_holiday_payment.py tests\test_premium_windows.py tests\test_logic.py -q`.
- [ ] להריץ בדיקות פלט: `py -m pytest tests\test_gesher_export_preview.py tests\test_pdf_renderer.py tests\test_guide_reports_excel_export.py tests\test_guide_payment_period_rows.py -q`.
- [ ] להריץ `py -m pytest tests -q` ולטפל בכשלונות לא־פלייקיים.
- [ ] להפעיל שרת מקומי ולבדוק דוח יומי וסיכום חודשי של 09/2026, כולל 20–21.09, בלי לכתוב נתוני ייצור.
- [ ] להריץ את שני סקריפטי ה־audit בייצור בקריאה בלבד ולשמור totals ו־checksum לצורך אישור.
- [ ] לבצע review ממוקד על גבולות זמן, כפל חלונות, היסטוריית תעריפים, scope של מערך דיור ו־N+1 queries.
- [ ] לבדוק `git diff --check` ו־`git status --short`; לא לכלול artifacts, exports, לוגים או נתוני עובדים.
- [ ] להציג למשתמשת ledger סופי לפי מערך ומדריך ולקבל אישור נפרד לפני כל תיקון כספי או עדכון DB.

