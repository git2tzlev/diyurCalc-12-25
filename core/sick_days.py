"""
Sick day calculation logic for DiyurCalc.
Handles identification of sick day sequences and payment rate determination.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional


def _identify_sick_day_sequences(
    reports: List[Dict],
    prev_month_sick_dates: Optional[List[date]] = None,
) -> Dict[date, int]:
    """
    זיהוי רצפי ימי מחלה וקביעת מספר היום ברצף לכל תאריך.

    לפי חוק דמי מחלה:
    - יום ראשון: 0% תשלום
    - ימים 2-3: 50% תשלום
    - מיום 4 והלאה: 100% תשלום

    תאריכים רצופים (כולל ימי מנוחה) נחשבים כרצף אחד.
    הפסקה של יותר מיום אחד מתחילה רצף חדש.

    Args:
        reports: רשימת דיווחים מהדאטבייס (חודש נוכחי)
        prev_month_sick_dates: תאריכי מחלה מהחודש הקודם לצורך המשכיות רצף

    Returns:
        מילון {תאריך: מספר_יום_מחלה} (1, 2, 3, 4...) - רק עבור החודש הנוכחי
    """
    # איסוף כל התאריכים שיש בהם דיווח מחלה (חודש נוכחי)
    current_sick_dates = set()
    for r in reports:
        shift_name = r.get("shift_name") or ""
        if "מחלה" in shift_name:
            r_date = r.get("date")
            if r_date:
                if isinstance(r_date, datetime):
                    current_sick_dates.add(r_date.date())
                elif isinstance(r_date, date):
                    current_sick_dates.add(r_date)

    if not current_sick_dates:
        return {}

    # איחוד תאריכי חודש קודם עם חודש נוכחי לחישוב רצף מלא
    all_sick_dates = set(current_sick_dates)
    if prev_month_sick_dates:
        all_sick_dates.update(prev_month_sick_dates)

    # מיון לפי תאריך
    sorted_dates = sorted(all_sick_dates)

    # בניית מילון עם מספר יום לכל תאריך
    all_day_numbers: Dict[date, int] = {}
    day_in_sequence = 1

    for i, d in enumerate(sorted_dates):
        if i == 0:
            all_day_numbers[d] = 1
        else:
            prev_date = sorted_dates[i - 1]
            # אם ההפרש הוא יום אחד בדיוק - המשך רצף
            if (d - prev_date).days == 1:
                day_in_sequence += 1
            else:
                # הפסקה - התחלת רצף חדש
                day_in_sequence = 1
            all_day_numbers[d] = day_in_sequence

    # החזרת רק תאריכי החודש הנוכחי
    return {d: num for d, num in all_day_numbers.items() if d in current_sick_dates}


def get_sick_payment_rate(sick_day_number: int) -> float:
    """
    קביעת אחוז התשלום לפי מספר יום המחלה ברצף.

    Args:
        sick_day_number: מספר היום ברצף (1, 2, 3, 4...)

    Returns:
        אחוז התשלום (0.0, 0.5, או 1.0)
    """
    if sick_day_number == 1:
        return 0.0  # יום ראשון - 0%
    elif sick_day_number <= 3:
        return 0.5  # ימים 2-3 - 50%
    else:
        return 1.0  # מיום 4 והלאה - 100%
