"""Event-driven retroactive salary completion calculations."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Optional

from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS
from core.logic import calculate_monthly_summary
from services.gesher_difference import (
    build_completion_gesher_rows,
    build_gesher_lines_from_summary,
    compare_line_sets,
    finalize_completion_rows,
)


logger = logging.getLogger(__name__)

PENDING_STATUSES = ("open", "included_in_export")
SUPPORTED_TABLES = ("time_reports", "payment_components")
VALID_TRANSITIONS = {
    "open": {"included_in_export", "cancelled", "ignored"},
    "included_in_export": {"open", "exported", "cancelled"},
}


def _parse_snapshot(value: Any) -> Optional[dict[str, Any]]:
    if not value:
        return None
    result = dict(value)
    raw_date = result.get("date")
    if isinstance(raw_date, str):
        try:
            result["date"] = date.fromisoformat(raw_date[:10])
        except ValueError:
            pass
    for field in ("created_at", "updated_at", "approved_at", "payment_marked_at"):
        raw_value = result.get(field)
        if isinstance(raw_value, str):
            try:
                result[field] = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                pass
    return result


def _event_error(event: dict[str, Any]) -> str:
    if event.get("source_table") not in SUPPORTED_TABLES:
        return "סוג מקור אינו נתמך"
    if not event.get("person_id"):
        return "חסר מדריך"
    # בלי קוד מירב לא נבנית שום שורת גשר למדריך, וההשלמה הייתה נעלמת בשקט
    if not str(event.get("meirav_code") or "").strip():
        return "חסר קוד מירב למדריך"
    if not event.get("work_year") or not event.get("work_month"):
        return "חסר חודש עבודה"
    if not event.get("payment_year") or not event.get("payment_month"):
        return "חסר חודש תשלום"
    action = str(event.get("source_action") or "").upper()
    if action == "INSERT" and not event.get("new_data"):
        return "חסרה תמונת נתונים חדשה"
    if action == "UPDATE" and (not event.get("old_data") or not event.get("new_data")):
        return "חסרה תמונת לפני או אחרי"
    if action == "DELETE" and not event.get("old_data"):
        return "חסרה תמונת נתונים ישנה"
    if action not in {"INSERT", "UPDATE", "DELETE"}:
        return "פעולת מקור אינה נתמכת"
    if action == "UPDATE":
        old_data = event.get("old_data") or {}
        new_data = event.get("new_data") or {}
        old_date = old_data.get("date")
        new_date = new_data.get("date")
        if old_date and new_date and (old_date.year, old_date.month) != (new_date.year, new_date.month):
            return "שינוי תאריך בין חודשי עבודה דורש פיצול ידני"
        if old_data.get("person_id") != new_data.get("person_id"):
            return "שינוי מדריך ברשומה דורש פיצול ידני"
        if old_data.get("apartment_id") != new_data.get("apartment_id"):
            return "שינוי דירה ברשומה דורש פיצול ידני"
    return ""


def is_manual_completion_event(event: dict[str, Any]) -> bool:
    """האם האירוע שייך לרכיב שההשלמה שלו משולמת ידנית ולכן אינה יוצאת לגשר."""
    if event.get("source_table") != "payment_components":
        return False
    data = event.get("new_data") or event.get("old_data") or {}
    component_type_id = data.get("component_type_id")
    if component_type_id in (None, ""):
        return False
    return int(component_type_id) in MANUAL_COMPLETION_COMPONENT_TYPE_IDS


def get_salary_impact_events(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    housing_array_id: Optional[int] = None,
    company_code: Optional[str] = None,
    person_ids: Optional[set[int]] = None,
    statuses: Iterable[str] = PENDING_STATUSES,
) -> list[dict[str, Any]]:
    """Load supported impact events with display and scope metadata in one query."""
    params: list[Any] = [payment_year, payment_month, list(statuses)]
    filters = [
        "sie.payment_year = %s",
        "sie.payment_month = %s",
        "sie.status = ANY(%s)",
        "sie.source_table = ANY(%s)",
    ]
    params.append(list(SUPPORTED_TABLES))
    if housing_array_id is not None:
        filters.append("sie.housing_array_id = %s")
        params.append(housing_array_id)
    if company_code:
        filters.append("COALESCE(e.code, '001') = %s")
        params.append(str(company_code))
    if person_ids:
        filters.append("sie.person_id = ANY(%s)")
        params.append(list(person_ids))

    rows = conn.execute(f"""
        SELECT sie.*, p.name AS person_name, p.meirav_code, p.email AS person_email,
               COALESCE(e.code, '001') AS employer_code,
               ap.name AS apartment_name, ha.name AS housing_array_name,
               CASE
                   WHEN sie.source_table = 'time_reports' THEN st.name
                   WHEN sie.source_table = 'payment_components' THEN pct.name
                   ELSE NULL
               END AS source_item_name
        FROM salary_impact_events sie
        LEFT JOIN people p ON p.id = sie.person_id
        LEFT JOIN employers e ON e.id = p.employer_id
        LEFT JOIN apartments ap ON ap.id = sie.apartment_id
        LEFT JOIN housing_arrays ha ON ha.id = sie.housing_array_id
        LEFT JOIN shift_types st ON st.id = NULLIF(
            COALESCE(sie.new_data ->> 'shift_type_id', sie.old_data ->> 'shift_type_id'),
            ''
        )::integer
        LEFT JOIN payment_component_types pct ON pct.id = NULLIF(
            COALESCE(sie.new_data ->> 'component_type_id', sie.old_data ->> 'component_type_id'),
            ''
        )::integer
        WHERE {' AND '.join(filters)}
        ORDER BY sie.work_year, sie.work_month, p.name, sie.created_at, sie.id
    """, tuple(params)).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["old_data"] = _parse_snapshot(event.get("old_data"))
        event["new_data"] = _parse_snapshot(event.get("new_data"))
        event["validation_error"] = _event_error(event)
        events.append(event)
    return events


def _reverse_events(events: Iterable[dict[str, Any]]) -> tuple[dict, dict]:
    report_overrides: dict[int, Optional[dict]] = {}
    component_overrides: dict[int, Optional[dict]] = {}
    ordered = sorted(
        events,
        key=lambda event: (event.get("created_at") or datetime.min, int(event["id"])),
        reverse=True,
    )
    for event in ordered:
        source_id = int(event["source_id"])
        target = report_overrides if event["source_table"] == "time_reports" else component_overrides
        action = str(event["source_action"]).upper()
        target[source_id] = None if action == "INSERT" else dict(event["old_data"])
    return report_overrides, component_overrides


def _calculate_group_lines(
    conn,
    work_year: int,
    work_month: int,
    person_ids: set[int],
    *,
    report_overrides: dict[int, Optional[dict]],
    component_overrides: dict[int, Optional[dict]],
) -> list[dict[str, Any]]:
    raw_conn = conn.conn if hasattr(conn, "conn") else conn
    summary_data, _ = calculate_monthly_summary(
        raw_conn,
        work_year,
        work_month,
        person_ids=person_ids,
        time_report_overrides=report_overrides,
        payment_component_overrides=component_overrides,
    )
    return build_gesher_lines_from_summary(
        conn,
        summary_data,
        work_year,
        work_month,
        company_code=None,
        person_ids=person_ids,
        include_negative_values=True,
    )


def build_salary_impact_completion_rows(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    statuses: Iterable[str] = ("included_in_export",),
    housing_array_id: Optional[int] = None,
    company_code: Optional[str] = None,
    person_ids: Optional[set[int]] = None,
    request_cache: Optional[dict] = None,
) -> dict[str, Any]:
    """Calculate completion rows from event snapshots without persisting amounts."""
    selected_statuses = tuple(sorted(set(statuses)))
    cache_key = (
        "salary-impact", payment_year, payment_month, selected_statuses,
        housing_array_id, company_code, tuple(sorted(person_ids or set())),
    )
    if request_cache is not None and cache_key in request_cache:
        return request_cache[cache_key]

    started = time.perf_counter()
    calculation_statuses = tuple(sorted(set(PENDING_STATUSES) | set(selected_statuses)))
    all_events = get_salary_impact_events(
        conn,
        payment_year,
        payment_month,
        housing_array_id=housing_array_id,
        company_code=company_code,
        person_ids=person_ids,
        statuses=calculation_statuses,
    )
    valid_events = [event for event in all_events if not event["validation_error"]]
    invalid_events = [event for event in all_events if event["validation_error"]]
    selected_events = [event for event in valid_events if event["status"] in selected_statuses]
    unselected_events = [event for event in valid_events if event["status"] not in selected_statuses]

    all_by_month: dict[tuple[int, int], list[dict]] = defaultdict(list)
    selected_by_month: dict[tuple[int, int], list[dict]] = defaultdict(list)
    unselected_by_month: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for event in valid_events:
        all_by_month[(int(event["work_year"]), int(event["work_month"]))].append(event)
    for event in selected_events:
        selected_by_month[(int(event["work_year"]), int(event["work_month"]))].append(event)
    for event in unselected_events:
        unselected_by_month[(int(event["work_year"]), int(event["work_month"]))].append(event)

    diffs = []
    group_rows: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for work_key, month_selected in sorted(selected_by_month.items()):
        work_year, work_month = work_key
        affected_people = {int(event["person_id"]) for event in month_selected}
        before_report, before_component = _reverse_events(all_by_month[work_key])
        after_report, after_component = _reverse_events(unselected_by_month.get(work_key, []))
        before_lines = _calculate_group_lines(
            conn, work_year, work_month, affected_people,
            report_overrides=before_report,
            component_overrides=before_component,
        )
        after_lines = _calculate_group_lines(
            conn, work_year, work_month, affected_people,
            report_overrides=after_report,
            component_overrides=after_component,
        )
        month_diffs = compare_line_sets(before_lines, after_lines)
        for diff in month_diffs:
            diff["work_year"] = work_year
            diff["work_month"] = work_month
        diffs.extend(month_diffs)
        for row in finalize_completion_rows(build_completion_gesher_rows(month_diffs)):
            person_id = row.get("person_id")
            if person_id is not None:
                group_rows[(int(person_id), work_year, work_month)].append(row)

    rows = build_completion_gesher_rows(diffs)
    if company_code:
        rows = [row for row in rows if str(row.get("employer_code") or "001") == str(company_code)]
    finalize_completion_rows(rows)

    elapsed = time.perf_counter() - started
    if elapsed > 3:
        logger.warning(
            "Salary impact calculation exceeded target: period=%04d-%02d groups=%d elapsed=%.2fs",
            payment_year, payment_month, len(selected_by_month), elapsed,
        )
    result = {
        "rows": rows,
        "diffs": diffs,
        "events": all_events,
        "selected_events": selected_events,
        "invalid_events": invalid_events,
        "group_rows": dict(group_rows),
        "elapsed_seconds": elapsed,
    }
    if request_cache is not None:
        request_cache[cache_key] = result
    return result


def update_salary_impact_group_status(
    conn,
    *,
    payment_year: int,
    payment_month: int,
    person_id: int,
    from_status: str,
    to_status: str,
    actor_person_id: Optional[int],
    work_year: Optional[int] = None,
    work_month: Optional[int] = None,
    export_file_id: Optional[int] = None,
    housing_array_id: Optional[int] = None,
) -> int:
    """Move a guide's events through their lifecycle, for one work month or all of them."""
    if to_status not in VALID_TRANSITIONS.get(from_status, set()):
        raise ValueError("מעבר סטטוס השלמה אינו חוקי")
    events = get_salary_impact_events(
        conn,
        payment_year,
        payment_month,
        person_ids={person_id},
        statuses=(from_status,),
        housing_array_id=housing_array_id,
    )
    matching = [
        event for event in events
        if (work_year is None or int(event.get("work_year") or 0) == work_year)
        and (work_month is None or int(event.get("work_month") or 0) == work_month)
    ]
    if not matching:
        return 0
    if to_status == "included_in_export":
        invalid = [event for event in matching if event["validation_error"]]
        if invalid:
            raise ValueError("לא ניתן לאשר קבוצה עם אירועים חסרים או לא תקינים")
    event_ids = [int(event["id"]) for event in matching]
    exported_sql = ""
    params: list[Any] = [to_status, actor_person_id]
    if to_status == "exported":
        exported_sql = ", exported_at = NOW(), exported_by = %s, export_batch_id = %s"
        params.extend([actor_person_id, export_file_id])
    params.extend([event_ids, from_status])
    cursor = conn.execute(f"""
        UPDATE salary_impact_events
        SET status = %s, updated_at = NOW(), updated_by = %s {exported_sql}
        WHERE id = ANY(%s) AND status = %s
    """, tuple(params))
    return cursor.rowcount
