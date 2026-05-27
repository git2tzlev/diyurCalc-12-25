# תכנית למערכת השנייה - היסטוריית דריסת שעות משמרת חול

מסמך זה מגדיר את חוזה הנתונים שהמערכת השנייה צריכה לממש עבור שינוי שעות
ב-`shift_time_overrides`.

## מטרה

כאשר משנים שעות דריסה של משמרת חול, החישוב במערכת השכר צריך להישאר נכון גם
לחודשים שכבר נסגרו או שולמו.

המערכת הראשית קוראת את ההיסטוריה לפי חודש חישוב:

- הטבלה הראשית `shift_time_overrides` מחזיקה את המצב הנוכחי.
- הטבלה `shift_time_overrides_history` מחזיקה את הערכים הישנים.
- `year` ו-`month` בשורת ההיסטוריה הם החודש שממנו הערך הישן הפסיק להיות תקף.

## טבלה

```sql
CREATE TABLE IF NOT EXISTS shift_time_overrides_history (
    id SERIAL PRIMARY KEY,
    original_override_id INTEGER NULL,
    shift_type_id INTEGER NOT NULL,
    apartment_id INTEGER NULL,
    housing_array_id INTEGER NULL,
    start_time TIME NULL,
    end_time TIME NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
);
```

אינדקסים לחסימת שינוי כפול לאותו חודש:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_time_overrides_history_original_month
    ON shift_time_overrides_history (original_override_id, year, month)
    WHERE original_override_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_time_overrides_history_apartment_month
    ON shift_time_overrides_history (shift_type_id, apartment_id, year, month)
    WHERE original_override_id IS NULL AND apartment_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_time_overrides_history_housing_month
    ON shift_time_overrides_history (shift_type_id, housing_array_id, year, month)
    WHERE original_override_id IS NULL AND housing_array_id IS NOT NULL;
```

## שמירת שינוי שעות

כאשר משתמש משנה override קיים:

1. לקבל מהמשתמש חודש תחולה, למשל `05/2026`.
2. לבדוק שלא קיימת כבר שורת היסטוריה לאותו `original_override_id`, `year`, `month`.
3. לשמור בטבלת ההיסטוריה את הערך הישן, לפני העדכון.
4. לעדכן את `shift_time_overrides` לערך החדש.

דוגמה:

מצב נוכחי לפני שינוי:

```text
override_id=12, apartment_id=10, start_time=16:00, end_time=08:00
```

משנים מ-`05/2026` ל-`15:00-08:00`.

שורת היסטוריה:

```text
original_override_id=12
shift_type_id=103
apartment_id=10
housing_array_id=NULL
start_time=16:00
end_time=08:00
is_active=true
year=2026
month=5
```

ואז הטבלה הראשית מתעדכנת ל:

```text
start_time=15:00
end_time=08:00
```

## שינוי שני בחודש אחר

אם ב-`08/2026` משנים שוב ל-`17:00-08:30`, מוסיפים שורת היסטוריה נוספת:

```text
original_override_id=12
start_time=15:00
end_time=08:00
year=2026
month=8
```

לא דורסים את השורה של `05/2026`.

התוצאה בחישוב:

- `04/2026` - יקבל `16:00-08:00`
- `05/2026` עד `07/2026` - יקבל `15:00-08:00`
- `08/2026` והלאה - יקבל `17:00-08:30`

## חסימת שינוי כפול באותו חודש

אם קיימת שורת היסטוריה עבור אותו override ואותו חודש תחולה, המערכת השנייה צריכה
לעצור ולהציג שגיאה.

דוגמת בדיקה:

```sql
SELECT id
FROM shift_time_overrides_history
WHERE original_override_id = :override_id
  AND year = :year
  AND month = :month
LIMIT 1;
```

אם נמצאה שורה, לא לבצע עדכון. הודעה מומלצת:

```text
כבר בוצע שינוי שעות לדריסה זו בחודש שנבחר. יש לבחור חודש תחולה אחר.
```

האינדקסים הייחודיים מגבים את הכלל הזה גם ברמת DB.

## יצירת override חדש

אם יוצרים override חדש שתקף רק מחודש מסוים, צריך לשמור היסטוריה שמייצגת מצב קודם
לא פעיל:

```text
original_override_id=<id החדש אחרי יצירה>
shift_type_id=103
apartment_id=<אם זו דריסת דירה>
housing_array_id=<אם זו ברירת מחדל למערך>
start_time=NULL
end_time=NULL
is_active=false
year=<חודש תחולה>
month=<חודש תחולה>
```

כך חישוב חודשים קודמים לא יראה override שלא היה קיים אז.

## ביטול override

אם מבטלים override מחודש מסוים:

1. שומרים בהיסטוריה את הערך הפעיל הישן.
2. מסמנים בטבלה הראשית `is_active=false`, או מוחקים רק אם המערכת השנייה מתחייבת
   לשמור את כל ההיסטוריה לפני המחיקה.

מומלץ לסמן `is_active=false` ולא למחוק, כדי לשמור קשר ברור ל-`original_override_id`.

## שדות חובה בשורת היסטוריה

- `original_override_id` - id של השורה בטבלה הראשית, כאשר קיים.
- `shift_type_id` - כיום צריך להיות `103`.
- `apartment_id` או `housing_array_id` - לפי סוג הדריסה.
- `start_time`, `end_time` - הערך הישן.
- `is_active` - האם הערך הישן היה פעיל.
- `year`, `month` - חודש התחולה של השינוי החדש.
- `created_by` - המשתמש שביצע את השינוי, אם ידוע.
