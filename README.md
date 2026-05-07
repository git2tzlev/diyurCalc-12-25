# DiyurCalc - מערכת חישוב שכר ומשמרות

מערכת web פנימית לניהול משמרות, חישוב שכר, הפקת דוחות, ייצוא שכר ושליחת מיילים למדריכים בדיור. האפליקציה בנויה ב-FastAPI, משתמשת ב-PostgreSQL, ומציגה ממשק Jinja2 עם מסכי מדריך, דשבורד סטטיסטיקות וייצוא גשר.

## מה המערכת כוללת

- חישוב שכר חודשי לפי דיווחי עבודה, כוננויות, חופשה, מחלה ורכיבי תשלום נוספים
- דפי מדריך עם פירוט רצפים, סיכום חודשי, PDF ותצוגה מקדימה
- סיכום חודשי רוחבי לכלל המדריכים
- ייצוא גשר ו-Excel
- דשבורד סטטיסטיקות לפי מערכי דיור, דירות ומדריכים
- ניהול קודי תשלום, נעילת חודשים והגדרות מייל
- מצב דמו עם בסיס נתונים נפרד

## טכנולוגיות

- Python 3.9+
- FastAPI
- PostgreSQL עם `psycopg2`
- Jinja2 templates
- Pandas ו-OpenPyXL לייצוא Excel
- `xhtml2pdf` ו-Edge/Chrome headless ליצירת PDF

## מבנה הפרויקט

```text
diyur003/
├── app.py                      # נקודת הכניסה ורישום כל ה-routes
├── app_utils.py                # מקור האמת לחישוב יומי וחודשי
├── core/
│   ├── auth.py                 # sessions, login והרשאות
│   ├── config.py               # טעינת תצורה מ-.env
│   ├── constants.py            # קבועים עסקיים משותפים
│   ├── database.py             # pools, demo mode, housing filter
│   ├── history.py              # שליפות היסטוריות ונעילת חודשים
│   ├── holiday_payment.py      # חישוב תשלום חג
│   ├── logic.py                # facade לחישובים ו-summary רוחבי
│   ├── sick_days.py            # חוקי דמי מחלה
│   └── time_utils.py           # זמן, שבת, חג ופורים
├── routes/                     # שכבת HTTP והעמודים
├── services/                   # Gesher, email, PDF
├── templates/                  # תבניות Jinja2
├── static/                     # קבצים סטטיים
├── tests/                      # בדיקות יחידה ואינטגרציה לוגית
├── docs/                       # תיעוד טכני והיסטורי
├── scripts/                    # סקריפטי תחזוקה וחקירה
├── requirements.txt
├── start.bat
└── Procfile
```

## דרישות מערכת

- Python 3.9 ומעלה
- PostgreSQL נגיש דרך `DATABASE_URL`
- אופציונלי: `DEMO_DATABASE_URL` עבור מצב דמו
- מומלץ: Edge או Chrome מותקן לצורך יצירת PDF headless

## התקנה והרצה מקומית

### 1. יצירת סביבה וירטואלית

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. התקנת תלויות

```powershell
py -m pip install -r requirements.txt
```

### 3. הגדרת `.env`

צור קובץ `.env` בשורש הפרויקט. המשתנים החשובים בפועל:

```env
DATABASE_URL=postgresql://username:password@host:5432/dbname

# אופציונלי - נדרש רק אם משתמשים במצב דמו
DEMO_DATABASE_URL=postgresql://username:password@host:5432/demo_db
DEMO_MODE_PASSWORD=change-me

HOST=0.0.0.0
PORT=8000
DEBUG=True
SECRET_KEY=change-me

ENABLE_CACHING=True
CACHE_TIMEOUT=300
DEFAULT_EXPORT_ENCODING=utf-8
```

הערות:

- המערכת לא תומכת כיום ב-SQLite.
- שכר מינימום, תעריפי משמרות, קודי תשלום והגדרות מייל נשמרים במסד הנתונים, לא ב-`.env`.
- ללא `DATABASE_URL` האפליקציה לא תעלה.

### 4. הרצה

ב-Windows:

```powershell
py -m uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

או דרך:

```powershell
.\start.bat
```

אם `python` קיים ב-`PATH`, אפשר גם:

```powershell
python app.py
```

האפליקציה תהיה זמינה ב-`http://localhost:8000`.

## אימות והרשאות

- כניסה מתבצעת דרך `/login`
- רק תפקידים `super_admin` ו-`framework_manager` יכולים להתחבר
- מנהל מסגרת מוגבל אוטומטית למערך הדיור שלו
- מצב דמו, פילטר מערך דיור ותקופה נבחרת נשמרים בעוגיות

## הערות תפעוליות חשובות

- יום עבודה מחושב מ-`08:00` עד `08:00` למחרת, לא מחצות עד חצות
- מקור האמת לחישוב השכר הוא `app_utils.py`
- נתונים היסטוריים מוחלים לפני חישוב השכר דרך `core/history.py`
- זמני שבת וחג נלקחים מטבלת `shabbat_times`
- תשלום חג מחושב בנפרד ב-`core/holiday_payment.py`
- הגדרות מייל מנוהלות דרך מסך admin ונשמרות בטבלת `email_settings`

## בדיקות

הרצת כל הבדיקות:

```powershell
py -m pytest tests -q
```

בדיקות ממוקדות:

```powershell
py -m pytest tests\test_logic.py -q
py -m pytest tests\test_salary_calculation.py -q
py -m pytest tests\test_holiday_payment.py -q
```

קומפילציה בסיסית:

```powershell
$files = @('app.py','app_utils.py') + (Get-ChildItem core,routes,services -Filter *.py | ForEach-Object { $_.FullName })
py -m py_compile @files
```

## פריסה

- `Procfile` מריץ את האפליקציה עם `uvicorn app:app`
- לפריסה יש להגדיר לפחות `DATABASE_URL` ו-`SECRET_KEY`
- אם משתמשים במצב דמו בפרודקשן, צריך להגדיר גם `DEMO_DATABASE_URL` ו-`DEMO_MODE_PASSWORD`

## מסמכים נוספים

- `PROJECT_DOCUMENTATION.md` - תמונת ארכיטקטורה ותפקידי המודולים
- `docs/LOGIC.md` - חוקי החישוב בפועל
- `CHANGELOG.md` - יומן השינויים הפעיל
- `docs/CHANGES_LOG.md` - ארכיון היסטורי ישן

## רישיון

הפרויקט הוא קניין פנימי של עמותת צהר.
