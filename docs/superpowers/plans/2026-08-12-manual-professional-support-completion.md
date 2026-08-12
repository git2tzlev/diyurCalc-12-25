# תוכנית יישום: השלמות תומך מקצועי לתשלום ידני

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** השלמות של רכיב "תומך מקצועי" יאושרו לייצוא אוטומטית ויוצגו בדוח המדריך ובמסך ההשלמות עם סכום, אך לא ייכתבו לקובץ הגשר ולא ייכנסו לאף סיכום כספי — כדי שכל סכום שמוצג במערכת ימשיך להשתוות לקובץ הגשר.

**Architecture:** היום `_completion_target_for_diff` מחזיר `None` לרכיב תומך מקצועי, ולכן ההשלמה נזרקת בשקט — אין לה סכום בשום מקום. התוכנית ממפה אותה לסמל יעד `243` לפי `internal_key`, וחוסמת אותה בשלוש נקודות: כתיבת קובץ ה-mrv, התצוגה המקדימה, והבדיקה מול הגשר. הסמל **לא** נרשם ב-`COMPLETION_EXPORT_CODES`, ולכן `add_completion_row_to_totals` מדלג עליו מעצמו ואף סיכום כספי לא מושפע. בנוסף, טריגר האודיט מסמן אירועים כאלה כ-`included_in_export` כבר ביצירה, כי אין מה לאשר לייצוא.

**Tech Stack:** Python 3.14, FastAPI, Jinja2, PostgreSQL (plpgsql triggers), pytest.

## Global Constraints

- קבועים חדשים המשותפים ליותר ממודול אחד נכתבים ב-`core/constants.py` בלבד (הנחיית `CLAUDE.md`).
- Type Hints לכל פונקציה; Docstrings בעברית לפונקציות ציבוריות; פונקציות עד 50 שורות.
- אין תאימות לאחור: משנים חתימה — מעדכנים את כל הקוראים, בלי wrapper.
- **אילוץ-על: תאימות מלאה בין מה שמוצג לגשר.** אף סכום ידני לא נכנס ל-`completion_retro_money_total`, `total_payment`, `gesher_total`, `display_total`, `rounded_total`, ולא לעמודות הסיכום הכללי. הוא מוצג רק כשורה בדוח ובמסך ההשלמות.
- מיפוי רכיבים לסמלי השלמה נעשה תמיד לפי `internal_key` ולא לפי סמל מקור — סמלי המקור ניתנים לעריכה במסך סמלי שכר ומיפוי לפי סמל נשבר בעריכה.
- הרצת בדיקות: `py -m pytest tests/ -q`. כל משימה מסתיימת בקומיט. `CHANGELOG.md` ו-`VERSION` מתעדכנים פעם אחת ב-Task 6.

## עובדות שאומתו מול בסיס הנתונים והקוד

- `payment_component_types` מכיל רק `13 = תומך מקצועי`. אין רכיב בשם "תומך מקצועי-השלמות".
- `payment_codes` ממפה `professional_support` → `merav_code = 243`. אין סמל רטרו להשלמות שלו — ולכן התשלום ידני.
- `_completion_target_for_diff` ([services/gesher_difference.py:266](../../../services/gesher_difference.py#L266)) מחזיר `None` ל-`professional_support`, והשורה נזרקת ב-`build_completion_gesher_rows:316`.
- `add_completion_row_to_totals` ([services/gesher_exporter.py:148](../../../services/gesher_exporter.py#L148)) יוצא מיד כאשר `COMPLETION_TOTAL_KEYS.get(symbol)` ריק. **זו הסיבה שאין לרשום את 243 ב-`COMPLETION_EXPORT_CODES`** — אי-הרישום הוא מנגנון ההגנה על הסיכומים, ולא השמטה.
- דוח המדריך כבר מציג את הרכיב, ב-`payments_data` עם `is_payment_period_completion=True` ותווית "שולם ב-MM/YYYY" ([routes/guide.py:1781-1802](../../../routes/guide.py#L1781)). **לא** ב-`completion_payments_data` — זה שדה של דירות השלמה, מושג אחר. אומת מול נתוני אמת: צרפתי (בטאט) הדר, חודש עבודה 06/2026, שורה "תומך מקצועי 33.33 ₪ · שולם ב-07/2026". אין צורך לבנות תצוגה חדשה.
- `salary_impact_events.status` מוגדר `DEFAULT 'open'` עם `CHECK` שמתיר `included_in_export` ([core/audit.py:140](../../../core/audit.py#L140)). האירוע נוצר בטריגר plpgsql ב-`core/audit.py:228`.

## מה מפורשות מחוץ להיקף

- **אין עמודה בסיכום הכללי** (`templates/general_summary.html`) ואין שורה בטבלת רכיבי השכר של דף המדריך (`templates/guide.html`). שני המסכים האלה מציגים סכומים שחייבים להשתוות לגשר.
- אין שינוי בהתנהגות התשלום החודשי הרגיל של תומך מקצועי, שכן ממשיך לצאת בגשר בסמל 243.

## מבנה הקבצים

| קובץ | אחריות | סוג שינוי |
|------|--------|-----------|
| `core/constants.py` | `MANUAL_COMPLETION_SYMBOLS`, `MANUAL_COMPLETION_COMPONENT_TYPE_IDS` — מקור אמת יחיד | הוספה |
| `services/gesher_difference.py` | מיפוי לסמל יעד, שם תצוגה, החרגה מהבדיקה מול הגשר ומחסימות הייצוא | שינוי |
| `services/gesher_exporter.py` | חסימת הסמל מקובץ ה-mrv ומהתצוגה המקדימה | שינוי |
| `services/salary_impact.py` | `is_manual_completion_event` — זיהוי אירוע של רכיב ידני | שינוי |
| `core/audit.py` | טריגר שמסמן אירועי רכיב ידני כ-`included_in_export` ביצירה | שינוי |
| `routes/completions.py` | דגל `is_manual` בתגיות הסכום | שינוי |
| `templates/completions.html` | תגית בלי מספר סמל לרכיב ידני | שינוי |
| `core/business_rules_catalog.py` | עדכון הכלל העסקי | שינוי |
| `tests/test_completion_manual_payment.py` | כל הבדיקות של הפיצ'ר | יצירה |

---

### Task 1: קבועים משותפים ומיפוי לסמל יעד

**Files:**
- Modify: `core/constants.py` (בסוף הקובץ)
- Modify: `services/gesher_difference.py:19-49` (קבועים), `:266-278` (`_completion_target_for_diff`)
- Test: `tests/test_completion_manual_payment.py`

**Interfaces:**
- Produces: `core.constants.MANUAL_COMPLETION_SYMBOLS: set[str]` — `{"243"}`
- Produces: `core.constants.MANUAL_COMPLETION_COMPONENT_TYPE_IDS: set[int]` — `{13}`
- Produces: `gesher_difference.COMPLETION_MANUAL_TARGETS: dict[str, str]` — `{"professional_support": "243"}`
- Produces: `_completion_target_for_diff(diff: dict) -> Optional[str]` מחזיר `"243"` כאשר `diff["internal_key"] == "professional_support"`

- [ ] **Step 1: כתיבת הבדיקות הנכשלות**

צור `tests/test_completion_manual_payment.py`:

```python
# -*- coding: utf-8 -*-
"""בדיקות להשלמות תומך מקצועי המשולמות ידנית ואינן יוצאות לגשר."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS, MANUAL_COMPLETION_SYMBOLS
from services import gesher_difference


def _professional_support_diff(amount: float = 150.0) -> dict:
    return {
        "internal_key": "professional_support",
        "symbol": "243",
        "employee_code": "1234",
        "employer_code": "001",
        "person_id": 1,
        "person_name": "אבי",
        "amount_diff": amount,
        "quantity_diff": 0.0,
        "rate": 0.0,
    }


class ManualCompletionTargetTests(unittest.TestCase):
    def test_professional_support_maps_to_manual_symbol(self):
        target = gesher_difference._completion_target_for_diff(_professional_support_diff())
        self.assertEqual(target, "243")

    def test_shared_constants_describe_the_manual_component(self):
        self.assertIn("243", MANUAL_COMPLETION_SYMBOLS)
        self.assertIn(13, MANUAL_COMPLETION_COMPONENT_TYPE_IDS)

    def test_mapping_is_by_internal_key_not_by_source_symbol(self):
        """עריכת סמל המקור במסך סמלי שכר לא אמורה לשבור את המיפוי."""
        diff = {**_professional_support_diff(), "symbol": "999"}
        self.assertEqual(gesher_difference._completion_target_for_diff(diff), "243")

    def test_professional_support_produces_a_completion_row(self):
        rows = gesher_difference.build_completion_gesher_rows([_professional_support_diff()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "243")
        self.assertEqual(rows[0]["amount"], 150.0)

    def test_row_gets_the_manual_display_name(self):
        rows = gesher_difference.finalize_completion_rows(
            gesher_difference.build_completion_gesher_rows([_professional_support_diff()])
        )
        self.assertEqual(rows[0]["display_name"], "תומך מקצועי - לתשלום ידני")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: הרצה לאימות כישלון**

Run: `py -m pytest tests/test_completion_manual_payment.py -v`
Expected: FAIL — `ImportError: cannot import name 'MANUAL_COMPLETION_SYMBOLS'`

- [ ] **Step 3: הוספת הקבועים המשותפים**

בסוף `core/constants.py`:

```python
# =============================================================================
# Manual Completion Payments
# =============================================================================

# רכיבים שההשלמה שלהם משולמת ידנית במירב: אין להם סמל רטרו, ולכן ההשלמה
# מחושבת ומוצגת בדוח ובמסך ההשלמות אך לעולם אינה נכתבת לקובץ הגשר.
MANUAL_COMPLETION_SYMBOLS = {"243"}
MANUAL_COMPLETION_COMPONENT_TYPE_IDS = {13}  # תומך מקצועי
```

- [ ] **Step 4: מיפוי הרכיב לסמל היעד**

ב-`services/gesher_difference.py`, אחרי `COMPLETION_PASSTHROUGH_TARGETS` (שורה 37):

```python
# רכיבים שההשלמה שלהם משולמת ידנית: מחושבים ומוצגים, אך נחסמים בכתיבה לגשר.
# המיפוי לפי מפתח פנימי, כמו COMPLETION_PASSTHROUGH_TARGETS ומאותה סיבה.
COMPLETION_MANUAL_TARGETS = {
    "professional_support": "243",
}
```

ב-`COMPLETION_TARGET_DISPLAY_NAMES` הוסף:

```python
    "243": "תומך מקצועי - לתשלום ידני",
```

והחלף את `_completion_target_for_diff` כולה:

```python
def _completion_target_for_diff(diff: dict[str, Any]) -> Optional[str]:
    """סמל היעד של שורת הפרש - רטרו ייעודי, תשלום ידני, אחרת פנסיה/לא פנסיה."""
    internal_key = str(diff.get("internal_key") or "").strip()
    passthrough = COMPLETION_PASSTHROUGH_TARGETS.get(internal_key)
    if passthrough:
        return passthrough
    manual = COMPLETION_MANUAL_TARGETS.get(internal_key)
    if manual:
        return manual
    source_symbol = str(diff.get("symbol") or "").strip()
    if source_symbol in COMPLETION_PENSION_SOURCE_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["pension"]
    if source_symbol in COMPLETION_NON_PENSION_SOURCE_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["non_pension"]
    return None
```

- [ ] **Step 5: הרצה לאימות הצלחה**

Run: `py -m pytest tests/test_completion_manual_payment.py -v`
Expected: PASS — 5 בדיקות

- [ ] **Step 6: קומיט**

```bash
git add core/constants.py services/gesher_difference.py tests/test_completion_manual_payment.py
git commit -m "feat: map professional support completions to a manual payment symbol"
```

---

### Task 2: חסימה מקובץ הגשר, מהתצוגה המקדימה ומכל הסיכומים

**Files:**
- Modify: `services/gesher_exporter.py` (ייבוא), `:59-108` (`append_completion_rows_to_preview`), `:240-271` (`_write_completion_rows`)
- Test: `tests/test_completion_manual_payment.py`

**Interfaces:**
- Consumes: `MANUAL_COMPLETION_SYMBOLS` מ-Task 1
- Produces: שתי הפונקציות מדלגות על שורות שהסמל שלהן ב-`MANUAL_COMPLETION_SYMBOLS`

**שתי אזהרות קריטיות:**
1. **אין להשתמש ב-`EXCLUDED_EXPORT_CODES`.** הוא חוסם סמל בכל הקשר, וסמל 243 של התשלום החודשי הרגיל **כן** חייב להמשיך לצאת בגשר. החסימה חלה על שורות השלמה בלבד.
2. **אין לרשום את 243 ב-`COMPLETION_EXPORT_CODES`.** אי-הרישום הוא מה שגורם ל-`add_completion_row_to_totals` לצאת מוקדם ולא לזהם אף סיכום. הבדיקה ב-Step 1 מקבעת את ההתנהגות הזאת.

- [ ] **Step 1: כתיבת הבדיקות הנכשלות**

הוסף ל-`tests/test_completion_manual_payment.py`:

```python
import io

from services import gesher_exporter


def _manual_completion_row(amount: float = 150.0) -> dict:
    return {
        "employer_code": "001",
        "employee_code": "001234",
        "person_id": 1,
        "person_name": "אבי",
        "symbol": "243",
        "rate": amount,
        "amount": amount,
        "quantity": 0.0,
        "display_name": "תומך מקצועי - לתשלום ידני",
        "source_symbols": "243",
    }


def _regular_completion_row(amount: float = 90.0) -> dict:
    return {
        **_manual_completion_row(amount),
        "symbol": "253",
        "display_name": "הפרשי השלמות לא לפנסיה",
        "source_symbols": "371",
    }


class ManualCompletionIsNotExportedTests(unittest.TestCase):
    def test_manual_row_is_not_written_to_the_gesher_file(self):
        output = io.StringIO()
        written = gesher_exporter._write_completion_rows(
            output, [_manual_completion_row(), _regular_completion_row()]
        )
        self.assertEqual(written, 1)
        self.assertNotIn("243", output.getvalue())

    def test_regular_completion_row_is_still_written(self):
        output = io.StringIO()
        gesher_exporter._write_completion_rows(output, [_regular_completion_row()])
        self.assertIn("253", output.getvalue())

    def test_manual_row_is_not_shown_in_the_export_preview(self):
        preview = [{"person_id": 1, "name": "אבי", "meirav_code": "001234", "lines": []}]
        gesher_exporter.append_completion_rows_to_preview(
            preview, [_manual_completion_row(), _regular_completion_row()]
        )
        symbols = [line["symbol"] for line in preview[0]["lines"]]
        self.assertEqual(symbols, ["253"])

    def test_manual_row_alone_does_not_create_a_preview_card(self):
        preview = []
        gesher_exporter.append_completion_rows_to_preview(preview, [_manual_completion_row()])
        self.assertEqual(preview, [])


class ManualCompletionDoesNotTouchTotalsTests(unittest.TestCase):
    """אילוץ-העל: כל סכום שמוצג במערכת חייב להשתוות לקובץ הגשר."""

    def test_manual_symbol_is_not_registered_as_an_export_code(self):
        self.assertNotIn("243", gesher_exporter.COMPLETION_EXPORT_CODES)

    def test_manual_row_leaves_every_money_total_untouched(self):
        totals = {
            **gesher_exporter.empty_completion_totals(),
            "total_payment": 1000.0,
            "gesher_total": 1000.0,
            "display_total": 1000.0,
            "rounded_total": 1000.0,
        }
        before = dict(totals)
        gesher_exporter.add_completion_row_to_totals(totals, _manual_completion_row(150.0))
        self.assertEqual(totals, before)

    def test_regular_completion_still_updates_the_money_totals(self):
        totals = {**gesher_exporter.empty_completion_totals(), "total_payment": 1000.0}
        gesher_exporter.add_completion_row_to_totals(totals, _regular_completion_row(90.0))
        self.assertEqual(totals["completion_non_pension"], 90.0)
        self.assertEqual(totals["completion_retro_money_total"], 90.0)
        self.assertEqual(totals["total_payment"], 1090.0)
```

- [ ] **Step 2: הרצה לאימות כישלון**

Run: `py -m pytest tests/test_completion_manual_payment.py -v`
Expected: FAIL — 4 בדיקות ב-`ManualCompletionIsNotExportedTests` נכשלות. שלוש הבדיקות ב-`ManualCompletionDoesNotTouchTotalsTests` אמורות לעבור כבר עכשיו — הן מקבעות התנהגות קיימת שאסור לשבור בהמשך.

- [ ] **Step 3: הוספת הייבוא והחסימה**

ב-`services/gesher_exporter.py`, שנה את שורת הייבוא של הקבועים:

```python
from core.constants import MANUAL_COMPLETION_SYMBOLS, TZOHAR_HALEV_HOUSING_ARRAY_ID
```

ב-`_write_completion_rows`, כשורה הראשונה בתוך הלולאה:

```python
    for row in rows:
        # השלמות שמשולמות ידנית מחושבות ומוצגות, אך אינן נכתבות לקובץ
        if str(row.get("symbol") or "") in MANUAL_COMPLETION_SYMBOLS:
            continue
        person_name = row.get("person_name") or ""
```

ב-`append_completion_rows_to_preview`, כשורה הראשונה בתוך הלולאה:

```python
    for row in completion_rows:
        # התצוגה המקדימה חייבת להיות זהה לקובץ, ולכן מדלגת על שורות ידניות
        if str(row.get("symbol") or "") in MANUAL_COMPLETION_SYMBOLS:
            continue
        person = by_person_id.get(row.get("person_id"))
```

- [ ] **Step 4: הרצה לאימות הצלחה**

Run: `py -m pytest tests/test_completion_manual_payment.py -v`
Expected: PASS — 12 בדיקות

- [ ] **Step 5: אימות שלא נשברו בדיקות הייצוא הקיימות**

Run: `py -m pytest tests/test_gesher_exporter_completions.py tests/test_gesher_export_preview.py -v`
Expected: PASS

- [ ] **Step 6: קומיט**

```bash
git add services/gesher_exporter.py tests/test_completion_manual_payment.py
git commit -m "feat: block manual completion symbols from the Gesher file and preview"
```

---

### Task 3: תגית בדף ההשלמות בלי מספר סמל

**Files:**
- Modify: `routes/completions.py` (ייבוא, `_completion_amount_badges`)
- Modify: `templates/completions.html` — שש תגיות: שלוש ברמת מדריך ושלוש ברמת חודש עבודה
- Test: `tests/test_completion_manual_payment.py`

**Interfaces:**
- Consumes: `MANUAL_COMPLETION_SYMBOLS` מ-Task 1
- Produces: לכל badge שדה `is_manual: bool`

**למה:** התגית מציגה היום `סמל 243: 150.00 ₪`. אם המזכירה תראה מספר סמל היא עלולה להקליד אותו במירב כשורת רטרו — וסמל רטרו כזה לא קיים.

- [ ] **Step 1: כתיבת הבדיקה הנכשלת**

הוסף ל-`tests/test_completion_manual_payment.py`:

```python
class ManualCompletionBadgeTests(unittest.TestCase):
    def test_manual_badge_is_flagged_so_the_symbol_can_be_hidden(self):
        from routes import completions as completion_routes

        badges = completion_routes._completion_amount_badges([
            {"symbol": "243", "display_name": "תומך מקצועי - לתשלום ידני",
             "amount": 150.0, "quantity": 0.0},
            {"symbol": "253", "display_name": "הפרשי השלמות לא לפנסיה",
             "amount": 90.0, "quantity": 0.0},
        ])
        by_symbol = {badge["symbol"]: badge for badge in badges}
        self.assertTrue(by_symbol["243"]["is_manual"])
        self.assertFalse(by_symbol["253"]["is_manual"])
```

- [ ] **Step 2: הרצה לאימות כישלון**

Run: `py -m pytest tests/test_completion_manual_payment.py::ManualCompletionBadgeTests -v`
Expected: FAIL — `KeyError: 'is_manual'`

- [ ] **Step 3: הוספת הדגל ל-badge**

ב-`routes/completions.py`, בייבוא:

```python
from core.constants import MANUAL_COMPLETION_SYMBOLS
```

ב-`_completion_amount_badges`, בתוך `badges.setdefault`:

```python
        badge = badges.setdefault(symbol, {
            "symbol": symbol,
            "display_name": row.get("display_name") or "הפרשי השלמות",
            "is_quantity": symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS,
            "is_manual": symbol in MANUAL_COMPLETION_SYMBOLS,
            "amount": 0.0,
            "quantity": 0.0,
        })
```

- [ ] **Step 4: הסתרת מספר הסמל בתבנית**

ב-`templates/completions.html` יש שש תגיות שמכילות את המחרוזת `סמל {{ badge.symbol }}:`. בכל אחת מהן החלף

```jinja
<bdi>סמל {{ badge.symbol }}:
```

ב-

```jinja
<bdi>{% if not badge.is_manual %}סמל {{ badge.symbol }}:{% endif %}
```

אתר אותן לפי המחרוזת ולא לפי מספר שורה — מספרי השורות זזים אחרי כל עריכה. לאימות שכולן טופלו:

```bash
py -c "
text = open('templates/completions.html', encoding='utf-8').read()
print('נותרו ללא תנאי:', text.count('<bdi>סמל'))
print('טופלו:', text.count('{% if not badge.is_manual %}'))
"
```
Expected: `נותרו ללא תנאי: 0`, `טופלו: 6`

- [ ] **Step 5: אימות התבנית והבדיקות**

```bash
py -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).parse(open('templates/completions.html', encoding='utf-8').read()); print('template OK')"
py -m pytest tests/test_completion_manual_payment.py tests/test_completion_report_tasks.py -v
```
Expected: `template OK` והבדיקות עוברות

- [ ] **Step 6: קומיט**

```bash
git add routes/completions.py templates/completions.html tests/test_completion_manual_payment.py
git commit -m "feat: hide the Merav symbol on manual completion badges"
```

---

### Task 4: החרגה מהבדיקה מול הגשר ומחסימות הייצוא

**Files:**
- Modify: `services/salary_impact.py` (ייבוא + פונקציה חדשה `is_manual_completion_event`)
- Modify: `services/gesher_difference.py` — `build_completion_gesher_audit` (בניית `expected_rows`) ו-`build_approved_completion_gesher_rows` (בניית `invalid_notices`)
- Test: `tests/test_completion_manual_payment.py`

**Interfaces:**
- Consumes: `MANUAL_COMPLETION_SYMBOLS`, `MANUAL_COMPLETION_COMPONENT_TYPE_IDS` מ-Task 1
- Produces: `salary_impact.is_manual_completion_event(event: dict[str, Any]) -> bool`
- Produces: `gesher_difference._drop_manual_completion_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]`

**שתי בעיות שהמשימה פותרת:**
1. `compare_completion_audit_rows` משווה שורות מצופות מול מה שנמצא בפועל בקובץ. שורה ידנית לעולם לא תופיע בקובץ, ולכן בלי החרגה כל תומך מקצועי יסומן "חסר" ויציף את "בדיקה מול הגשר" בממצאי שווא.
2. `build_approved_completion_gesher_rows` הופך כל אירוע מאושר שלא עבר ולידציה ל-block שחוסם את ייצוא הגשר. אחרי Task 5 אירועי תומך מקצועי מאושרים אוטומטית — כך שמדריך בלי קוד מירב היה חוסם את הייצוא של כל החברה בגלל תשלום שממילא לא יוצא בגשר.

- [ ] **Step 1: כתיבת הבדיקות הנכשלות**

הוסף ל-`tests/test_completion_manual_payment.py`:

```python
from services import salary_impact


def _manual_event(component_type_id=13, status="included_in_export") -> dict:
    return {
        "id": 1,
        "source_table": "payment_components",
        "status": status,
        "person_name": "אבי",
        "work_year": 2026,
        "work_month": 5,
        "validation_error": "חסר קוד מירב למדריך",
        "new_data": {"component_type_id": component_type_id},
        "old_data": None,
    }


class ManualCompletionEventTests(unittest.TestCase):
    def test_professional_support_component_event_is_manual(self):
        self.assertTrue(salary_impact.is_manual_completion_event(_manual_event()))

    def test_other_component_event_is_not_manual(self):
        self.assertFalse(salary_impact.is_manual_completion_event(_manual_event(component_type_id=2)))

    def test_shift_event_is_not_manual(self):
        event = {**_manual_event(), "source_table": "time_reports"}
        self.assertFalse(salary_impact.is_manual_completion_event(event))

    def test_deleted_component_is_read_from_the_old_snapshot(self):
        event = {**_manual_event(), "new_data": None, "old_data": {"component_type_id": 13}}
        self.assertTrue(salary_impact.is_manual_completion_event(event))

    def test_missing_component_type_is_not_manual(self):
        event = {**_manual_event(), "new_data": {"component_type_id": None}, "old_data": None}
        self.assertFalse(salary_impact.is_manual_completion_event(event))


class ManualCompletionAuditTests(unittest.TestCase):
    def test_manual_rows_are_dropped_from_the_expected_side(self):
        rows = gesher_difference._drop_manual_completion_rows([
            _manual_completion_row(), _regular_completion_row()
        ])
        self.assertEqual([row["symbol"] for row in rows], ["253"])

    def test_manual_row_is_not_reported_as_missing_from_the_bridge(self):
        entries = gesher_difference.compare_completion_audit_rows(
            gesher_difference._drop_manual_completion_rows([_manual_completion_row()]),
            [],
        )
        self.assertEqual(entries, [])
```

- [ ] **Step 2: הרצה לאימות כישלון**

Run: `py -m pytest tests/test_completion_manual_payment.py::ManualCompletionEventTests tests/test_completion_manual_payment.py::ManualCompletionAuditTests -v`
Expected: FAIL — `AttributeError: module 'services.salary_impact' has no attribute 'is_manual_completion_event'`

- [ ] **Step 3: זיהוי אירוע של רכיב ידני**

ב-`services/salary_impact.py`, בייבוא:

```python
from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS
```

ומתחת ל-`_event_error` (שורה 81):

```python
def is_manual_completion_event(event: dict[str, Any]) -> bool:
    """האם האירוע שייך לרכיב שההשלמה שלו משולמת ידנית ולכן אינה יוצאת לגשר."""
    if event.get("source_table") != "payment_components":
        return False
    data = event.get("new_data") or event.get("old_data") or {}
    component_type_id = data.get("component_type_id")
    if component_type_id in (None, ""):
        return False
    return int(component_type_id) in MANUAL_COMPLETION_COMPONENT_TYPE_IDS
```

- [ ] **Step 4: החרגה מהבדיקה מול הגשר**

ב-`services/gesher_difference.py`, בייבוא:

```python
from core.constants import MANUAL_COMPLETION_SYMBOLS
```

מעל `build_completion_gesher_audit`:

```python
def _drop_manual_completion_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """הסרת שורות שמשולמות ידנית - הן לעולם לא יופיעו בקובץ הגשר."""
    return [
        row for row in rows
        if str(row.get("symbol") or "") not in MANUAL_COMPLETION_SYMBOLS
    ]
```

ב-`build_completion_gesher_audit` עטוף את שתי בניות ה-`expected_rows`:

```python
        expected_rows = _drop_manual_completion_rows(
            finalize_completion_rows(build_completion_gesher_rows(event_diffs))
        )
```

```python
            expected_rows = _merge_completion_rows(
                expected_rows,
                _drop_manual_completion_rows(
                    finalize_completion_rows(build_completion_gesher_rows(legacy_diffs))
                ),
            )
```

- [ ] **Step 5: החרגה מחסימות הייצוא**

ב-`build_approved_completion_gesher_rows`, בייבוא המקומי שבראש הפונקציה:

```python
    from services.salary_impact import build_salary_impact_completion_rows, is_manual_completion_event
```

ובבניית `invalid_notices` הוסף תנאי:

```python
    invalid_notices = [
        {
            "type": "invalid_salary_impact_event",
            "message": event.get("validation_error"),
            "event_id": event.get("id"),
            "person_name": event.get("person_name") or "",
            "work_year": event.get("work_year"),
            "work_month": event.get("work_month"),
            "company_code": event.get("employer_code") or "001",
        }
        for event in event_result["invalid_events"]
        if event.get("status") == "included_in_export"
        # תשלום ידני לא יוצא בגשר, ולכן אירוע פגום שלו לא חוסם את הייצוא
        and not is_manual_completion_event(event)
    ]
```

- [ ] **Step 6: הרצה לאימות הצלחה**

Run: `py -m pytest tests/test_completion_manual_payment.py -v`
Expected: PASS — 20 בדיקות

- [ ] **Step 7: קומיט**

```bash
git add services/salary_impact.py services/gesher_difference.py tests/test_completion_manual_payment.py
git commit -m "feat: exclude manual completions from the Gesher audit and export blocks"
```

---

### Task 5: אישור אוטומטי לייצוא בעת יצירת האירוע

**Files:**
- Modify: `core/audit.py:150-250` (פונקציית הטריגר `capture_salary_impact`)
- Test: `tests/test_completion_manual_payment.py`

**Interfaces:**
- Consumes: `MANUAL_COMPLETION_COMPONENT_TYPE_IDS` מ-Task 1
- Produces: אירוע `salary_impact_events` של רכיב ידני נוצר עם `status = 'included_in_export'` במקום `'open'`

**למה:** אין מה לאשר לייצוא — התשלום לא יוצא בגשר בשום מקרה. דרישת אישור ידנית רק מסתירה את הסכום מהמזכירה עד שמישהו ילחץ על כפתור.

**איך לבנות את ה-SQL:** מזהי הרכיבים מגיעים מהקבוע ב-Python ומוטמעים במחרוזת הטריגר, כדי שלא ייווצר מקור אמת שני. `core/audit.py` מריץ `cursor.execute` בלי פרמטרים על מחרוזת ה-DDL, ולכן הטמעה ישירה בטוחה כאן.

- [ ] **Step 1: כתיבת הבדיקות הנכשלות**

הוסף ל-`tests/test_completion_manual_payment.py`:

```python
class ManualCompletionAutoApprovalTests(unittest.TestCase):
    """הטריגר מסמן אירוע של רכיב ידני כמאושר לייצוא כבר ביצירה."""

    def _trigger_sql(self) -> str:
        import inspect

        from core import audit

        return inspect.getsource(audit._ensure_salary_impact_capture)

    def test_trigger_selects_the_status_by_component_type(self):
        sql = self._trigger_sql()
        self.assertIn("status_value", sql)
        self.assertIn("'included_in_export'", sql)

    def test_trigger_embeds_the_manual_component_ids_from_the_shared_constant(self):
        sql = self._trigger_sql()
        for component_type_id in MANUAL_COMPLETION_COMPONENT_TYPE_IDS:
            self.assertIn(str(component_type_id), sql)
```

`_ensure_salary_impact_capture` היא הפונקציה שיוצרת את הטריגר (אומת). גוף הטריגר אינו מכיל אף תו `%` (אומת), ולכן ההמרה ל-f-string ב-Step 3 בטוחה.

- [ ] **Step 2: הרצה לאימות כישלון**

Run: `py -m pytest tests/test_completion_manual_payment.py::ManualCompletionAutoApprovalTests -v`
Expected: FAIL — `status_value` לא קיים במחרוזת הטריגר

- [ ] **Step 3: הוספת המשתנה לטריגר**

ב-`core/audit.py`, בייבוא:

```python
from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS
```

לפני `cursor.execute` של פונקציית הטריגר, בנה את רשימת המזהים:

```python
    manual_component_ids = ", ".join(
        str(component_type_id)
        for component_type_id in sorted(MANUAL_COMPLETION_COMPONENT_TYPE_IDS)
    )
```

הפוך את מחרוזת ה-DDL ל-f-string, הוסף `status_value` לרשימת ה-`DECLARE`:

```sql
            status_value text;
```

ואחרי השורה `domain_name := CASE NEW.table_name ...` הוסף:

```sql
            -- רכיבים שההשלמה שלהם משולמת ידנית אינם יוצאים לגשר, ולכן אין מה לאשר
            status_value := CASE
                WHEN NEW.table_name = 'payment_components'
                     AND NULLIF(row_data ->> 'component_type_id', '')::integer
                         IN ({manual_component_ids})
                THEN 'included_in_export'
                ELSE 'open'
            END;
```

הוסף `status` לרשימת העמודות ב-`INSERT INTO salary_impact_events (...)` ואת `status_value` במקום המקביל ב-`VALUES (...)`.

**אזהרה:** אל תוסיף תווי `%` לגוף הטריגר. psycopg2 מפרש `%` כסמן פרמטר, וגוף הטריגר הנוכחי נקי ממנו (אומת).

- [ ] **Step 4: הרצה לאימות הצלחה**

Run: `py -m pytest tests/test_completion_manual_payment.py -v`
Expected: PASS — 22 בדיקות

- [ ] **Step 5: החלת הטריגר על בסיס הנתונים ואימות ידני**

הטריגר נוצר מחדש בעליית השרת דרך `core/runtime_defaults.py`. הפעל את השרת, ואז:

```bash
py -m uvicorn app:app --reload --port 8000
```

במסך המדריך, סמן רכיב תשלום מסוג "תומך מקצועי" לחודש תשלום מאוחר, ובדוק:

```bash
py -c "
from dotenv import load_dotenv; load_dotenv()
from core.database import get_conn
with get_conn() as conn:
    rows = conn.execute('''
        SELECT id, status, new_data ->> 'component_type_id' AS component_type_id
        FROM salary_impact_events
        WHERE source_table = 'payment_components'
        ORDER BY id DESC LIMIT 5
    ''').fetchall()
    for row in rows:
        print(dict(row))
"
```
Expected: לרשומה עם `component_type_id = 13` הסטטוס הוא `included_in_export`; לרכיבים אחרים `open`

- [ ] **Step 6: קומיט**

```bash
git add core/audit.py tests/test_completion_manual_payment.py
git commit -m "feat: auto-approve manual completion events on creation"
```

---

### Task 6: אימות הדוח, כלל עסקי, תיעוד וגרסה

**Files:**
- Modify: `core/business_rules_catalog.py:1180`
- Modify: `CHANGELOG.md` (רשומה חדשה בראש), `core/config.py:24` (`VERSION`)

- [ ] **Step 1: אימות שהדוח כבר מציג את הרכיב**

אין קוד לכתוב כאן — יש לאמת. `completion_payments_data` ב-`routes/guide.py:1804` נבנה מ-`payment_components` שסומנו לחודש תשלום מאוחר, ו-`routes/guide.py:1758` מקבץ תומך מקצועי לשורה אחת. הרץ את השרת, פתח דוח מדריך שיש לו רכיב תומך מקצועי מסומן לחודש תשלום מאוחר, וודא שהשורה מופיעה בטבלת הדוח עם התווית של חודש התשלום.

אם השורה **לא** מופיעה — עצור, אל תמשיך לתיעוד, ודווח על הממצא. הנחת התוכנית הייתה שהדוח כבר מטפל בזה.

- [ ] **Step 2: עדכון הכלל העסקי**

ב-`core/business_rules_catalog.py`, החלף את השורה

```python
                    "ימי עבודה 767, תשלום חג 254, דמי הבראה 38, ביגוד 107, תוספות לפנסיה 379 ותומך מקצועי 243 אינם מועברים כרגע כהשלמות.",
```

בשתי שורות:

```python
                    "ימי עבודה 767, תשלום חג 254, דמי הבראה 38, ביגוד 107 ותוספות לפנסיה 379 אינם מועברים כרגע כהשלמות.",
                    "השלמת תומך מקצועי מאושרת לייצוא אוטומטית ומוצגת בדוח המדריך ובמסך ההשלמות, אך אינה נכתבת לקובץ הגשר ואינה נכנסת לאף סיכום כספי: אין לה סמל רטרו במירב והמזכירה משלמת אותה ידנית.",
```

- [ ] **Step 3: הרצת כל הבדיקות**

Run: `py -m pytest tests/ -q --ignore=tests/debug_payment_gap.py --ignore=tests/debug_2000_gap.py --ignore=tests/debug_dec_gap.py --ignore=tests/debug_dec_detailed.py --ignore=tests/debug_old_vs_new.py --ignore=tests/debug_payment_breakdown.py --ignore=tests/debug_extras.py`
Expected: PASS — אם משהו נכשל, תקן לפני המשך. אין לרשום ב-CHANGELOG הצלחה שלא אומתה.

- [ ] **Step 4: עדכון CHANGELOG וגרסה**

שנה את `core/config.py:24` מ-`VERSION: str = "2.33.0"` ל-`VERSION: str = "2.34.0"` (פיצ'ר חדש → Minor), והוסף בראש `CHANGELOG.md` אחרי ה-`---` הראשון:

```markdown
## [2.34.0] - 2026-08-12

### שינויים
- **השלמת תומך מקצועי - תשלום ידני:** הפרשי השלמות על רכיב תומך מקצועי מחושבים כעת ומוצגים בדוח המדריך ובמסך ההשלמות, אך נחסמים מקובץ הגשר ומהתצוגה המקדימה
- אירוע השלמה של תומך מקצועי נוצר בסטטוס "מאושר לייצוא" אוטומטית - אין מה לאשר, התשלום ממילא ידני
- **הסכום אינו נכנס לאף סיכום כספי:** לא ל-`completion_retro_money_total`, לא ל-`total_payment`/`gesher_total`, ולא לעמודות הסיכום הכללי. כל סכום שמוצג במערכת ממשיך להשתוות לקובץ הגשר
- המיפוי מתבצע לפי `internal_key` ולא לפי סמל מקור, כדי שעריכת סמל במסך סמלי שכר לא תשבור אותו
- תגיות של רכיב ידני מוצגות בלי מספר סמל, כדי שלא יוקלד במירב סמל רטרו שאינו קיים
- הבדיקה מול הגשר מתעלמת משורות ידניות, ואירוע פגום של רכיב ידני אינו חוסם עוד את ייצוא הגשר
- נוספו 22 בדיקות
- קבצים: `core/constants.py`, `core/audit.py`, `services/gesher_difference.py`, `services/gesher_exporter.py`, `services/salary_impact.py`, `routes/completions.py`, `templates/completions.html`, `core/business_rules_catalog.py`, `tests/test_completion_manual_payment.py`

### סיבה
להשלמות של תומך מקצועי אין סמל רטרו במירב, ולכן המזכירה משלמת אותן ידנית. עד היום `_completion_target_for_diff` החזיר `None` לרכיב הזה והשורה נזרקה בשקט - היא אמנם לא יצאה בגשר, אבל גם לא הופיע לה סכום בשום מסך, כך שלא היה ממה לשלם ידנית.

---
```

- [ ] **Step 5: קומיט**

```bash
git add core/business_rules_catalog.py CHANGELOG.md core/config.py
git commit -m "docs: document manual professional support completions"
```

---

## אימות סופי

- [ ] `py -m pytest tests/ -q` עובר
- [ ] סימון רכיב תומך מקצועי לחודש תשלום מאוחר יוצר אירוע בסטטוס `included_in_export`
- [ ] דף `/completions` מציג תגית "תומך מקצועי - לתשלום ידני" עם סכום ובלי מספר סמל
- [ ] דוח המדריך מציג את השורה עם תווית חודש התשלום
- [ ] `/export/gesher/preview` **לא** מציג את השורה
- [ ] קובץ ה-mrv המיוצא **לא** מכיל שורת 243 שמקורה בהשלמה, אך **כן** מכיל שורות 243 חודשיות רגילות
- [ ] "בדיקה מול הגשר" לא מדווחת על תומך מקצועי כ"חסר"
- [ ] סה"כ המשכורת בדף המדריך, בסיכום הכללי ובתצוגה המקדימה זהה לסכום קובץ הגשר

## סיכונים ידועים

- **סמל 243 משמש בשני הקשרים.** התשלום החודשי הרגיל של תומך מקצועי יוצא בגשר בסמל 243 ואסור לחסום אותו. החסימה חלה אך ורק על שורות השלמה, בשתי הפונקציות של Task 2. אין להוסיף את 243 ל-`EXCLUDED_EXPORT_CODES`.
- **אי-רישום 243 ב-`COMPLETION_EXPORT_CODES` הוא מנגנון הגנה, לא השמטה.** הוא מה שמונע מהסכום להיכנס לסיכומים. מי שיוסיף אותו שם בעתיד ישבור את אילוץ-העל. הבדיקה `test_manual_symbol_is_not_registered_as_an_export_code` מקבעת זאת.
- **Task 5 נוגע בטריגר DB.** הטריגר נוצר מחדש בעליית השרת. אירועים שכבר נוצרו לפני השינוי יישארו בסטטוס `open` וידרשו אישור ידני חד-פעמי. אם רוצים לעדכן אותם רטרואקטיבית — זו פעולת DB נפרדת שאינה בהיקף התוכנית.
