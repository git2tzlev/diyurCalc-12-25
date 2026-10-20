# Retry Email Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** להציג מעקב חי ואמין לשליחה מחדש של דוחות שנכשלו, כולל המדריכים שדולגו באצווה המקורית והסיבה לכך.

**Architecture:** מסלול SSE מאובטח ישתמש באותה בחירת נמענים ובאותו service של המסלול הקיים, וישדר `start`, `sending`, `progress`, `complete` ו־`error`. ה־UI יעדכן את אותה רשימת מדריכים ויציג בסיום סיכום כולל של האצווה המקורית והניסיון החוזר.

**Tech Stack:** Python, FastAPI/Starlette StreamingResponse, Jinja2, JavaScript EventSource, unittest/pytest.

**Spec:** בקשת המשתמשת בשיחה וכללי `AGENTS.md` שסופקו למשימה.

## Global Constraints

- לשמור הרשאה, scope של מערך דיור וטוקן חתום בצד השרת.
- לא לשלוח מחדש רשומות `sent` או `skipped`; רק `failed` יישלחו מחדש.
- לא לחשוף הודעות שגיאה טכניות או פרטי SMTP ללקוח.
- לשמר את מסלול ה־POST הקיים לתאימות.

## Review Focus

- אצווה עם `failed` וגם `skipped`: רק הכשלונות נשלחים, והמדולגים מופיעים בסיכום.
- לוגים חסרים: מזהי הגיבוי עוברים validation ו־scope לפני שליחה.
- טוקן לא תקין: לא נפתחת גישת DB ולא מתבצעת שליחה.
- תוצאת retry שנכשלת שוב: נשמרת ברשימת הכשלונות וניתן לנסות שוב.
- ניתוק EventSource: הממשק חוזר למצב שמאפשר ניסיון נוסף.

---

### Task 1: חוזה SSE לשליחה מחדש

**Files:**
- Modify: `tests/test_email_security.py`
- Modify: `routes/email.py`

**Interfaces:**
- Consumes: `process_guide_for_bulk(...)`, `get_email_logs(...)`, action token ו־housing scope קיימים.
- Produces: `retry_failed_stream(request, batch_id, year, month, token, failed_guide_ids) -> StreamingResponse`.

- [x] לכתוב בדיקה כושלת שבודקת אירועי התחלה, התקדמות וסיום, וש־`skipped` מקורי אינו נשלח מחדש.
- [x] להריץ את הבדיקה ולוודא שהיא נכשלת בגלל המסלול החסר.
- [x] לממש helper משותף לבחירת כשלונות וזרם SSE עם סיכום `sent`, `failed`, `skipped`.
- [x] להריץ את הבדיקה ולוודא שהיא עוברת.

### Task 2: רישום המסלול ועדכון הממשק

**Files:**
- Modify: `app.py`
- Modify: `templates/reports.html`

**Interfaces:**
- Consumes: אירועי SSE של Task 1.
- Produces: ניסיון חוזר עם פס התקדמות, סטטוס לכל מדריך וסיכום כולל.

- [x] לרשום endpoint מסוג GET עם אותם מנגנוני auth middleware והטוקן החתום.
- [x] להחליף את קריאת ה־POST ב־EventSource ולעדכן את הפריטים והסיכום בכל אירוע.
- [x] לוודא שהסיום מציג כמה נשלחו בסך הכול, מי עדיין נכשל ומי דולג ולמה.

### Task 3: אימות

**Files:**
- Test: `tests/test_email_security.py`
- Test: `tests/test_pdf_renderer.py`

**Interfaces:**
- Consumes: כל השינויים במשימה.
- Produces: עדות קומפילציה ובדיקות ללא שליחת מייל אמיתי.

- [x] להריץ בדיקות מייל/PDF ממוקדות וקומפילציה לקבצי Python ששונו.
- [x] להריץ `py -m pytest tests -q`.
- [x] לבדוק `git status --short` ולהפריד בין שינויי המשימה ללכלוך קודם.
