# PROJECT_DOCUMENTATION

מסמך זה מתאר את הארכיטקטורה הנוכחית של פרויקט `diyur003` כפי שהיא ממומשת בקוד היום. הוא נועד לספק תמונת על למי שנכנס לפרויקט, לאתר במהירות את מקור האמת של כל שכבה, ולהפחית כניסה לאזורים לא נכונים בקוד.

## תמונת על

DiyurCalc היא מערכת פנימית לניהול משמרות וחישוב שכר למדריכים בדיור. המערכת בנויה כיישום FastAPI עם שכבת תצוגה ב-Jinja2, בסיס נתונים PostgreSQL, וחישוב שכר מורכב הכולל:

- שעות רגילות ושעות נוספות
- שבת, חג ופורים
- כוננויות וקיזוזי כוננות
- משמרות לילה, תגבור, ליווי בי"ח וליווי רפואי
- חופשה, מחלה ותשלום חג
- ייצוא גשר, Excel, PDF ושליחת מיילים

## מקור האמת לחישוב

החישוב בפועל מתבצע היום בשני שלבים עיקריים, שניהם ב-`app_utils.py`:

1. `get_daily_segments_data(...)`
2. `aggregate_daily_segments_to_monthly(...)`

הפונקציות ב-`core/logic.py` אינן מנוע חישוב חלופי. הן עוטפות את `app_utils.py`, מוסיפות bulk loading, cache ושליפות רוחביות, ומשמשות את ה-routes ואת הייצוא.

המשמעות המעשית: כשיש שינוי לוגי בחישוב השכר, צריך להתחיל ב-`app_utils.py`.

## מבנה שכבות

### 1. כניסה ורישום routes

`app.py` אחראי על:

- יצירת מופע FastAPI
- רישום filters ל-Jinja2
- רישום middleware
- חיבור כל ה-routes
- endpoint-ים מערכתיים כמו `health`, `login`, `toggle demo mode`
- startup/shutdown

בנוסף, `app.py` מוודא ב-startup שקודי תשלום מסוימים קיימים גם במסד הראשי וגם במסד הדמו.

### 2. תצורה

`core/config.py` מרכז את כל הגדרות הסביבה:

- `DATABASE_URL`
- `DEMO_DATABASE_URL` דרך `core/database.py`
- `HOST`, `PORT`, `DEBUG`
- `SECRET_KEY`
- `ENABLE_CACHING`, `CACHE_TIMEOUT`
- `DEMO_MODE_PASSWORD`
- `DEFAULT_EXPORT_ENCODING`
- `LOCAL_TZ`

גרסת האפליקציה שמוצגת ב-UI מגיעה מ-`config.VERSION`.

### 3. מסד נתונים וקונטקסט בקשה

`core/database.py` אחראי על:

- connection pooling ל-production ול-demo
- מעבר בין DB ראשי ל-DB דמו
- context per request עבור `demo_mode`
- context per request עבור `housing_array_filter`
- helperים לעוגיות: מערך דיור, תקופה נבחרת, מצב דמו
- `get_conn()` שמחזיר wrapper תואם לעבודה מול PostgreSQL

המערכת משתמשת ב-PostgreSQL בלבד. אין כיום נתיב פעיל ל-SQLite.

### 4. אימות והרשאות

`core/auth.py` מטפל ב:

- אימות סיסמה
- session token חתום
- בדיקת תפקידים מורשים
- עזרי הרשאה ל-`super_admin` ול-`framework_manager`

ב-`app.py` יש `AuthMiddleware` שמחייב התחברות לכל הנתיבים חוץ מ:

- `/login`
- `/static`
- `/health`

אם המשתמש הוא `framework_manager`, המערכת כופה פילטר לפי `housing_array_id` שלו.

### 5. מנוע חישוב שכר

#### `app_utils.py`

זהו הקובץ המרכזי ביותר בפרויקט מבחינה עסקית.

אחריות עיקרית:

- שליפת דיווחים וסגמנטים
- החלת נתונים היסטוריים
- בניית סגמנטים יומיים
- פיצול רצפים וחישוב אחוזים
- תמחור לפי תעריפים, שבת, חג, פורים וסוג דירה
- אגרגציה חודשית לשדות היצוא והמסכים

#### `core/time_utils.py`

מרכז פונקציות זמן:

- המרת תאריכים וזמנים
- cache לזמני שבת
- סיווג יום: חול, ערב, יום קדוש
- גבולות שבת/חג
- גבולות פורים

#### `core/constants.py`

מקור האמת לקבועים העסקיים:

- מזהי סוגי משמרות
- מזהי סוגי דירה
- קבועי לילה
- קבועי כוננות
- קבועי carryover ו-breaks
- קבועי פורים

#### `core/history.py`

מטפל בנתונים שתוקפם משתנה לפי חודש:

- סטטוס עובד
- סוג דירה
- תעריפי משמרות לפי מערך דיור
- תעריפי כוננות
- שכר מינימום היסטורי
- נעילת חודשים

לוגיקת ההיסטוריה היא בגישת "valid until": רשומה היסטורית מגדירה עד איזה חודש הערך הישן היה תקף.

#### `core/sick_days.py`

מחשב רצפי ימי מחלה ואחוזי התשלום לפי חוק:

- יום 1 = 0%
- ימים 2-3 = 50%
- יום 4 ואילך = 100%

#### `core/holiday_payment.py`

מחשב תשלום חג למדריכים קבועים שלא עבדו בחג, על בסיס דיווחים חודשיים, סוג עובד וטבלת `shabbat_times`.

### 6. facade לחישוב רוחבי

`core/logic.py` מספק:

- `calculate_person_monthly_totals()` לעובד יחיד
- `calculate_monthly_summary()` לחישוב רוחבי לכלל המדריכים
- שליפות bulk של reports, segments ו-payment components
- שליפת קודי תשלום
- עזרי startup לקודי תשלום מובנים

בפועל, `calculate_monthly_summary()` משתמש ב-bulk loading כדי להקטין משמעותית את מספר השאילתות.

### 7. routes

#### `routes/home.py`

דף הבית:

- רשימת מדריכים פעילים
- בחירת חודש/שנה
- חיפוש לפי שם
- סינון לפי מערך דיור

#### `routes/guide.py`

המסך העשיר ביותר במערכת:

- דף מדריך מלא
- סיכום פשוט
- פירוט רצפים
- תצוגת PDF
- יצירת PDF
- שליחת דוחות במייל
- בדיקות הרשאה לפי מערך דיור

#### `routes/summary.py`

סיכום חודשי כללי לכלל המדריכים.

#### `routes/reports.py`

מסך ניהול דוחות ושליחת דוחות במייל לפי תקופה ומערך דיור.

#### `routes/export.py`

ייצוא:

- Gesher לכל המפעל
- Gesher לעובד בודד
- Gesher למספר עובדים
- תצוגה מקדימה ל-Gesher
- Excel

#### `routes/stats.py`

דשבורד סטטיסטיקות ו-API לגרפים. משתמש ב-cache פנימי על בסיס `calculate_monthly_summary()`.

#### `routes/admin.py`

פונקציות admin:

- ניהול קודי תשלום
- sync ל-DB דמו
- נעילה ופתיחה של חודש

#### `routes/email.py`

ניהול הגדרות מייל ושליחת דוחות.

#### `routes/auth.py`

עמודי login/logout והגדרת session cookie.

### 8. services

#### `services/gesher_exporter.py`

אחראי על:

- טעינת מיפוי קודי יצוא
- תרגום שדות חישוב לכמות/תעריף
- יצירת קבצי `.mrv`
- תצוגה מקדימה של יצוא

#### `services/email_service.py`

אחראי על:

- שמירת הגדרות SMTP ב-DB
- בדיקת תקינות חיבור SMTP
- יצירת PDF לדוחות
- שליחת דוחות בודדים ומרוכזים

יצירת PDF בדוחות מדריך נשענת בפועל על רינדור HTML והרצת Edge/Chrome headless.

## זרימות עיקריות במערכת

### זרימת login

1. המשתמש שולח תעודת זהות וסיסמה
2. `core/auth.py` מאמת משתמש מול `people`
3. נוצר session token חתום
4. `AuthMiddleware` משתמש ב-cookie בכל בקשה הבאה

### זרימת דף מדריך

1. route ב-`routes/guide.py` בודק הרשאה לפי מערך דיור
2. נשלפים reports, segments, cache לזמני שבת ושכר מינימום
3. `get_daily_segments_data()` בונה פירוט יומי
4. `aggregate_daily_segments_to_monthly()` בונה totals חודשיים
5. route מרנדר את הנתונים לתבנית

### זרימת סיכום חודשי כללי

1. `routes/summary.py` קורא ל-`calculate_monthly_summary()`
2. `core/logic.py` טוען bulk את כל הנתונים לחודש
3. לכל מדריך מחושב daily + monthly דרך `app_utils.py`
4. נבנים `summary_data` ו-`grand_totals`

### זרימת יצוא גשר

1. `routes/export.py` בודק הרשאות וסינון
2. `services/gesher_exporter.py` קורא ל-`calculate_monthly_summary()` או לחישוב פר-אדם
3. totals מתורגמים לסמלי גשר לפי `payment_codes`
4. נוצר קובץ `.mrv`

## כללים עסקיים מרכזיים

### יום עבודה

- יום עבודה מוגדר מ-`08:00` עד `08:00` למחרת
- נרמול זמנים לפני `08:00` מתבצע בתוך מנוע החישוב

### רצפים

- ברירת המחדל הנוכחית היא שהפסקה של `60` דקות ומעלה שוברת רצף
- יש לוגיקת תאימות היסטורית עבור חודשים ישנים יותר בקוד

### מדרגות שכר

- חול: 100%, 125%, 150%
- שבת/חג: 150%, 175%, 200%
- משמרת לילה משתמשת בספי 7/9 שעות במקום 8/10

### שבת, חג ופורים

- זמני שבת וחג מגיעים מטבלת `shabbat_times`
- פורים מחושב כיום בתעריף חג רק בין `08:00` ל-`22:00` באותו יום

### כוננות

- כוננות מתומחרת לפי סוג סגמנט, סוג דירה ומצב משפחתי
- אם חפיפה לעבודה היא לפחות 70%, הכוננות מתבטלת או מתקזזת לפי הכללים בקבועים

### נתונים היסטוריים

- סטטוס עובד, סוג דירה, תעריפים ושכר מינימום נלקחים לפי החודש המבוקש
- לכן שינוי נתון נוכחי לא בהכרח משפיע רטרואקטיבית

## טבלאות חשובות במסד הנתונים

- `people`
- `roles`
- `apartments`
- `apartment_types`
- `housing_arrays`
- `employers`
- `time_reports`
- `shift_types`
- `shift_time_segments`
- `shift_time_overrides`
- `shift_type_housing_rates_history`
- `standby_rates_history`
- `payment_components`
- `payment_codes`
- `minimum_wage_rates`
- `shabbat_times`
- `month_locks`
- `email_settings`

## בדיקות

קבצי בדיקות מרכזיים:

- `tests/test_logic.py`
- `tests/test_salary_calculation.py`
- `tests/test_holiday_payment.py`

הבדיקות מכסות בעיקר:

- מדרגות שכר
- שבת/חג
- carryover
- משמרות לילה
- חופשה/מחלה
- תשלום חג
- overrides למשמרת חול

## Hotspots לשינויים

הקבצים הרגישים ביותר לשינוי:

- `app_utils.py`
- `routes/guide.py`
- `core/time_utils.py`
- `core/history.py`

כל שינוי בהם דורש זהירות, בדיקות ממוקדות, והשוואה מול המסכים והיצוא.

## מסמכים משלימים

- `README.md` - הוראות הרצה והיכרות מהירה
- `docs/LOGIC.md` - חוקי החישוב בפועל
- `CHANGELOG.md` - יומן השינויים הפעיל
- `docs/CHANGES_LOG.md` - ארכיון שינויים היסטורי
