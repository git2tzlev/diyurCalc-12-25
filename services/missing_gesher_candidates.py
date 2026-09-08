"""Approval workflow for employees entirely absent from a final Gesher file."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Optional

from core.history import is_month_locked


CANDIDATE_STATUSES = ("pending", "approved", "rejected", "included_in_export", "cancelled")
MISSING_FINAL_PAYMENT_NOTE = "לא נכלל בגשר הסופי עקב קוד מירב חסר"


def is_missing_final_candidate_item(item: dict[str, Any]) -> bool:
    return MISSING_FINAL_PAYMENT_NOTE in str(item.get("payment_note") or "")


def was_code_missing_at_final(
    *,
    person_created_at: datetime,
    final_created_at: datetime,
    old_code_before_first_change: Optional[str],
) -> bool:
    """Exclude late records and code replacements; accept only a blank code at final time."""
    return (
        person_created_at <= final_created_at
        and not "".join(ch for ch in str(old_code_before_first_change or "") if ch.isdigit())
    )


def _candidate_had_missing_code_at_final(conn, candidate: dict[str, Any]) -> bool:
    row = conn.execute("""
        SELECT p.created_at AS person_created_at, gef.created_at AS final_created_at,
               (
                   SELECT al.old_data->>'meirav_code'
                   FROM audit_log al
                   WHERE al.table_name='people' AND al.record_id=p.id
                     AND al.changed_at > gef.created_at
                     AND jsonb_exists(al.changed_fields, 'meirav_code')
                   ORDER BY al.changed_at ASC
                   LIMIT 1
               ) AS old_code_before_first_change
        FROM people p JOIN gesher_export_files gef ON gef.id=%s
        WHERE p.id=%s
    """, (candidate["final_file_id"], candidate["person_id"])).fetchone()
    if not row or row["old_code_before_first_change"] is None:
        return False
    return was_code_missing_at_final(
        person_created_at=row["person_created_at"],
        final_created_at=row["final_created_at"],
        old_code_before_first_change=row["old_code_before_first_change"],
    )


def ensure_missing_gesher_candidates_table(conn) -> None:
    """Create the small persisted decision ledger used by the approval screen."""
    raw_conn = conn.conn if hasattr(conn, "conn") else conn
    cursor = raw_conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS missing_gesher_candidates (
            id BIGSERIAL PRIMARY KEY,
            payment_year INTEGER NOT NULL,
            payment_month INTEGER NOT NULL CHECK (payment_month BETWEEN 1 AND 12),
            work_year INTEGER NOT NULL,
            work_month INTEGER NOT NULL CHECK (work_month BETWEEN 1 AND 12),
            company_code TEXT NOT NULL,
            housing_array_id INTEGER NULL REFERENCES housing_arrays(id) ON DELETE SET NULL,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            employee_code TEXT NOT NULL,
            final_file_id BIGINT NOT NULL REFERENCES gesher_export_files(id) ON DELETE CASCADE,
            final_filename TEXT NOT NULL DEFAULT '',
            values_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            total_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
            snapshot_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (
                status IN ('pending','approved','rejected','included_in_export','cancelled')
            ),
            block_reason TEXT NULL,
            original_payment_periods JSONB NOT NULL DEFAULT '[]'::jsonb,
            approved_at TIMESTAMP NULL,
            approved_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
            rejected_at TIMESTAMP NULL,
            rejected_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
            included_at TIMESTAMP NULL,
            included_export_file_id BIGINT NULL REFERENCES gesher_export_files(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (payment_year, payment_month, work_year, work_month,
                    company_code, housing_array_id, person_id)
        )
    """)
    cursor.execute("""
        ALTER TABLE missing_gesher_candidates
        ADD COLUMN IF NOT EXISTS included_export_file_id BIGINT NULL
            REFERENCES gesher_export_files(id) ON DELETE SET NULL
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_missing_gesher_candidates_period_status
        ON missing_gesher_candidates (payment_year, payment_month, housing_array_id, status)
    """)
    raw_conn.commit()
    cursor.close()


def is_item_eligible_for_rollforward(
    payment_year: Optional[int],
    payment_month: Optional[int],
    target_year: int,
    target_month: int,
) -> bool:
    """Allow unmarked or previously missed items, never pull future items backwards."""
    if not payment_year or not payment_month:
        return True
    return int(payment_year) * 100 + int(payment_month) <= target_year * 100 + target_month


def source_item_belongs_to_candidate(
    *,
    item_date: date,
    payment_year: Optional[int],
    payment_month: Optional[int],
    work_year: int,
    work_month: int,
) -> bool:
    """Include current work-month items and older items assigned to its failed payment run."""
    item_month_key = item_date.year * 100 + item_date.month
    work_month_key = int(work_year) * 100 + int(work_month)
    if item_month_key == work_month_key:
        return True
    return (
        item_month_key < work_month_key
        and payment_year is not None
        and payment_month is not None
        and int(payment_year) == int(work_year)
        and int(payment_month) == int(work_month)
    )


def _merge_completion_rows(
    candidate: dict[str, Any],
    completion_rows: list[dict[str, Any]],
    *,
    factor: int,
) -> None:
    for row in completion_rows:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        current = candidate["values"].setdefault(symbol, {"amount": 0.0, "quantity": 0.0})
        current["amount"] = round(
            float(current.get("amount") or 0) + factor * float(row.get("amount") or 0), 2
        )
        current["quantity"] = round(
            float(current.get("quantity") or 0) + factor * float(row.get("quantity") or 0), 2
        )
    candidate["total_amount"] = round(sum(
        float(value.get("amount") or 0) for value in candidate["values"].values()
    ), 2)
    candidate["is_blocked"] = candidate["total_amount"] < -0.01
    candidate["block_reason"] = (
        "סכום ההשלמה שלילי ודורש בירור" if candidate["is_blocked"] else ""
    )


def merge_prior_completion_rows(
    candidate: dict[str, Any],
    completion_rows: list[dict[str, Any]],
) -> None:
    """Add prior-work-month completions that missed the candidate's final bridge."""
    _merge_completion_rows(candidate, completion_rows, factor=1)


def remove_current_payment_completion_rows(
    candidate: dict[str, Any],
    completion_rows: list[dict[str, Any]],
) -> None:
    """Keep items assigned to this payment month only in the regular completion section."""
    _merge_completion_rows(candidate, completion_rows, factor=-1)


def _add_prior_failed_payment_completions(
    conn,
    candidates: list[dict[str, Any]],
) -> None:
    """Enrich candidates from items assigned to the bridge month in which they were absent."""
    from core.payment_period import get_payment_period_completions
    from services.gesher_difference import (
        _build_current_completion_diffs,
        build_completion_gesher_rows,
        finalize_completion_rows,
    )

    completion_cache: dict[tuple[int, int, Optional[int]], list[dict[str, Any]]] = {}

    def payment_items(year: int, month: int, housing_array_id: Optional[int]):
        cache_key = (year, month, housing_array_id)
        if cache_key not in completion_cache:
            completion_cache[cache_key] = get_payment_period_completions(
                conn, year, month, housing_array_id=housing_array_id,
            )["items"]
        return completion_cache[cache_key]

    def completion_rows(candidate: dict[str, Any], items: list[dict[str, Any]]):
        rows: list[dict[str, Any]] = []
        by_work_month: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for item in items:
            by_work_month.setdefault(
                (int(item["work_year"]), int(item["work_month"])), []
            ).append(item)
        for (work_year, work_month), month_items in by_work_month.items():
            diffs = _build_current_completion_diffs(
                conn, work_year, work_month, candidate["company_code"], month_items,
                person_ids={int(candidate["person_id"])},
                diff_type="השלמה המשויכת לחודש תשלום",
            )
            rows.extend(finalize_completion_rows(build_completion_gesher_rows(diffs)))
        return rows

    for candidate in candidates:
        selected_payment_items = [
            item for item in payment_items(
                candidate["payment_year"], candidate["payment_month"],
                candidate["housing_array_id"],
            )
            if int(item["person_id"]) == int(candidate["person_id"])
            and int(item["work_year"]) == int(candidate["work_year"])
            and int(item["work_month"]) == int(candidate["work_month"])
        ]
        remove_current_payment_completion_rows(
            candidate, completion_rows(candidate, selected_payment_items)
        )

        prior_failed_items = [
            item for item in payment_items(
                candidate["work_year"], candidate["work_month"],
                candidate["housing_array_id"],
            )
            if int(item["person_id"]) == int(candidate["person_id"])
        ]
        merge_prior_completion_rows(
            candidate, completion_rows(candidate, prior_failed_items)
        )


def build_candidate_snapshots(
    audit_result: dict[str, Any],
    *,
    housing_array_id: Optional[int],
    include_person_keys: Optional[set[tuple[int, int, str, int]]] = None,
) -> list[dict[str, Any]]:
    """Collapse ``new_in_current`` audit rows to one approval candidate per employee."""
    grouped: dict[tuple[int, int, str, int], dict[str, Any]] = {}
    include_person_keys = include_person_keys or set()
    for group in audit_result.get("groups", []):
        file_id = group.get("file_id")
        if not file_id:
            continue
        for entry in group.get("entries", []):
            person_id = entry.get("person_id")
            if not person_id:
                continue
            key = (
                int(entry.get("work_year") or group.get("work_year")),
                int(entry.get("work_month") or group.get("work_month")),
                str(entry.get("company_code") or group.get("company_code") or ""),
                int(person_id),
            )
            if (
                entry.get("reason_category") != "new_in_current"
                and key not in include_person_keys
            ):
                continue
            candidate = grouped.setdefault(key, {
                "payment_year": int(audit_result["payment_year"]),
                "payment_month": int(audit_result["payment_month"]),
                "work_year": key[0],
                "work_month": key[1],
                "company_code": key[2],
                "housing_array_id": housing_array_id,
                "person_id": key[3],
                "person_name": entry.get("person_name") or "",
                "employee_code": entry.get("employee_code") or "",
                "final_file_id": int(file_id),
                "final_filename": group.get("filename") or entry.get("filename") or "",
                "values": {},
                "total_amount": 0.0,
                "status": "pending",
                "is_blocked": False,
                "block_reason": "",
            })
            symbol = str(entry.get("symbol") or "")
            if symbol:
                candidate["values"][symbol] = {
                    "amount": round(float(entry.get("actual_amount") or 0), 2),
                    "quantity": round(float(entry.get("actual_quantity") or 0), 2),
                }

    result = []
    for candidate in grouped.values():
        candidate["total_amount"] = round(sum(
            float(value["amount"]) for value in candidate["values"].values()
        ), 2)
        if candidate["total_amount"] < -0.01:
            candidate["is_blocked"] = True
            candidate["block_reason"] = "סכום ההשלמה שלילי ודורש בירור"
        result.append(candidate)
    return sorted(result, key=lambda row: (row["person_name"], row["person_id"]))


def _snapshot_hash(candidate: dict[str, Any]) -> str:
    payload = {
        "employee_code": candidate["employee_code"],
        "final_file_id": candidate["final_file_id"],
        "values": candidate["values"],
        "total_amount": candidate["total_amount"],
        "block_reason": candidate.get("block_reason") or "",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def sync_missing_gesher_candidates(
    conn,
    audit_result: dict[str, Any],
    *,
    housing_array_id: Optional[int],
) -> list[dict[str, Any]]:
    """Persist detected candidates while preserving unchanged human decisions."""
    ensure_missing_gesher_candidates_table(conn)
    existing_before = list_missing_gesher_candidates(
        conn, int(audit_result["payment_year"]), int(audit_result["payment_month"]),
        housing_array_id=housing_array_id,
    )
    approved_keys = {
        (row["work_year"], row["work_month"], row["company_code"], row["person_id"])
        for row in existing_before if row["status"] == "approved"
    }
    candidates = build_candidate_snapshots(
        audit_result,
        housing_array_id=housing_array_id,
        include_person_keys=approved_keys,
    )
    candidates = [
        candidate for candidate in candidates
        if (
            (candidate["work_year"], candidate["work_month"], candidate["company_code"],
             candidate["person_id"]) in approved_keys
            or _candidate_had_missing_code_at_final(conn, candidate)
        )
    ]
    _add_prior_failed_payment_completions(conn, candidates)
    seen_keys = []
    for candidate in candidates:
        fingerprint = _snapshot_hash(candidate)
        key_params = (
            candidate["payment_year"], candidate["payment_month"],
            candidate["work_year"], candidate["work_month"], candidate["company_code"],
            candidate["housing_array_id"], candidate["person_id"],
        )
        seen_keys.append(key_params)
        conn.execute("""
            INSERT INTO missing_gesher_candidates (
                payment_year, payment_month, work_year, work_month, company_code,
                housing_array_id, person_id, employee_code, final_file_id, final_filename,
                values_snapshot, total_amount, snapshot_hash, status, block_reason
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'pending',%s)
            ON CONFLICT (payment_year, payment_month, work_year, work_month,
                         company_code, housing_array_id, person_id)
            DO UPDATE SET
                employee_code = EXCLUDED.employee_code,
                final_file_id = EXCLUDED.final_file_id,
                final_filename = EXCLUDED.final_filename,
                values_snapshot = EXCLUDED.values_snapshot,
                total_amount = EXCLUDED.total_amount,
                block_reason = EXCLUDED.block_reason,
                status = CASE
                    WHEN missing_gesher_candidates.snapshot_hash = EXCLUDED.snapshot_hash
                    THEN missing_gesher_candidates.status ELSE 'pending' END,
                approved_at = CASE WHEN missing_gesher_candidates.snapshot_hash = EXCLUDED.snapshot_hash
                    THEN missing_gesher_candidates.approved_at ELSE NULL END,
                approved_by = CASE WHEN missing_gesher_candidates.snapshot_hash = EXCLUDED.snapshot_hash
                    THEN missing_gesher_candidates.approved_by ELSE NULL END,
                snapshot_hash = EXCLUDED.snapshot_hash,
                updated_at = NOW()
        """, (*key_params, candidate["employee_code"], candidate["final_file_id"],
              candidate["final_filename"], json.dumps(candidate["values"], ensure_ascii=False),
              candidate["total_amount"], fingerprint, candidate.get("block_reason") or None))

    existing = list_missing_gesher_candidates(
        conn, int(audit_result["payment_year"]), int(audit_result["payment_month"]),
        housing_array_id=housing_array_id,
    )
    active = {(c[2], c[3], c[4], c[6]) for c in seen_keys}
    for row in existing:
        row_key = (row["work_year"], row["work_month"], row["company_code"], row["person_id"])
        if row_key not in active and row["status"] != "included_in_export":
            conn.execute("""
                UPDATE missing_gesher_candidates
                SET status='cancelled', updated_at=NOW()
                WHERE id=%s
            """, (row["id"],))
    return list_missing_gesher_candidates(
        conn, int(audit_result["payment_year"]), int(audit_result["payment_month"]),
        housing_array_id=housing_array_id,
    )


def list_missing_gesher_candidates(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    housing_array_id: Optional[int],
) -> list[dict[str, Any]]:
    ensure_missing_gesher_candidates_table(conn)
    where = ["mgc.payment_year=%s", "mgc.payment_month=%s"]
    params: list[Any] = [payment_year, payment_month]
    if housing_array_id is not None:
        where.append("mgc.housing_array_id=%s")
        params.append(housing_array_id)
    rows = conn.execute(f"""
        SELECT mgc.*, p.name AS person_name
        FROM missing_gesher_candidates mgc
        JOIN people p ON p.id=mgc.person_id
        WHERE {' AND '.join(where)}
        ORDER BY p.name, mgc.work_year, mgc.work_month
    """, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def update_missing_gesher_candidate_status(
    conn,
    candidate_id: int,
    *,
    action: str,
    actor_person_id: Optional[int],
    housing_array_id: Optional[int],
) -> dict[str, Any]:
    """Approve/reject one employee atomically; approval rolls eligible source items."""
    if action not in {"approve", "reject", "reopen"}:
        raise ValueError("פעולת האישור אינה תקינה")
    ensure_missing_gesher_candidates_table(conn)
    where = ["id=%s"]
    params: list[Any] = [candidate_id]
    if housing_array_id is not None:
        where.append("housing_array_id=%s")
        params.append(housing_array_id)
    row = conn.execute(
        f"SELECT * FROM missing_gesher_candidates WHERE {' AND '.join(where)} FOR UPDATE",
        tuple(params),
    ).fetchone()
    if not row:
        raise ValueError("המועמד להשלמה לא נמצא")
    candidate = dict(row)
    if candidate.get("block_reason"):
        raise ValueError(str(candidate["block_reason"]))
    if candidate["status"] == "included_in_export":
        raise ValueError("ההשלמה כבר נכללה בגשר")

    if action in {"reject", "reopen"}:
        new_status = "rejected" if action == "reject" else "pending"
        if action == "reject" and candidate["status"] == "approved":
            for original in candidate.get("original_payment_periods") or []:
                table_name = original.get("table")
                if table_name not in {"time_reports", "payment_components"}:
                    continue
                conn.execute(f"""
                    UPDATE {table_name}
                    SET payment_year=%s, payment_month=%s, payment_note=%s,
                        payment_marked_at=%s, payment_marked_by=%s
                    WHERE id=%s AND payment_year=%s AND payment_month=%s
                """, (
                    original.get("payment_year"), original.get("payment_month"),
                    original.get("payment_note"), original.get("payment_marked_at"),
                    original.get("payment_marked_by"), original.get("id"),
                    candidate["payment_year"], candidate["payment_month"],
                ))
        conn.execute("""
            UPDATE missing_gesher_candidates
            SET status=%s, rejected_at=CASE WHEN %s='rejected' THEN NOW() ELSE NULL END,
                rejected_by=CASE WHEN %s='rejected' THEN %s ELSE NULL END,
                approved_at=NULL, approved_by=NULL, updated_at=NOW()
            WHERE id=%s
        """, (new_status, new_status, new_status, actor_person_id, candidate_id))
        candidate["status"] = new_status
        return candidate

    if is_month_locked(conn.conn if hasattr(conn, "conn") else conn,
                       candidate["payment_year"], candidate["payment_month"]):
        raise ValueError("חודש התשלום נעול; יש לבחור חודש תשלום פתוח")
    person = conn.execute("""
        SELECT p.meirav_code, e.code AS company_code
        FROM people p JOIN employers e ON e.id=p.employer_id
        WHERE p.id=%s AND p.housing_array_id=%s
    """, (candidate["person_id"], candidate["housing_array_id"])).fetchone()
    if not person or not str(person.get("meirav_code") or "").strip():
        raise ValueError("לא ניתן לאשר ללא קוד מירב")
    if str(person["company_code"]) != str(candidate["company_code"]):
        raise ValueError("המפעל של העובד השתנה ודורש בדיקה מחדש")
    current_code = "".join(ch for ch in str(person["meirav_code"]) if ch.isdigit()).zfill(6)
    if current_code != str(candidate["employee_code"]):
        raise ValueError("קוד מירב השתנה; יש להריץ איתור מחדש ולאשר שוב")
    duplicate = conn.execute("""
        SELECT COUNT(*) AS count
        FROM people p JOIN employers e ON e.id=p.employer_id
        WHERE p.housing_array_id=%s AND e.code=%s AND p.id<>%s
          AND regexp_replace(COALESCE(p.meirav_code,''), '\\D', '', 'g') =
              regexp_replace(%s, '\\D', '', 'g')
    """, (candidate["housing_array_id"], candidate["company_code"],
          candidate["person_id"], person["meirav_code"])).fetchone()
    if duplicate and int(duplicate["count"] or 0):
        raise ValueError("קוד מירב כפול באותו מפעל ומערך")
    final_file = conn.execute("""
        SELECT content FROM gesher_export_files
        WHERE id=%s AND is_final=TRUE AND COALESCE(is_cancelled,FALSE)=FALSE
          AND company_code=%s AND housing_array_id=%s
    """, (candidate["final_file_id"], candidate["company_code"],
          candidate["housing_array_id"])).fetchone()
    if not final_file:
        raise ValueError("קובץ הגשר הסופי השתנה; יש להריץ איתור מחדש")
    from services.gesher_difference import parse_gesher_file_lines
    if current_code in {
        line["employee_code"] for line in parse_gesher_file_lines(final_file["content"] or "")
    }:
        raise ValueError("העובד כבר מופיע בגשר הסופי")

    start = date(candidate["work_year"], candidate["work_month"], 1)
    end = date(candidate["work_year"] + (candidate["work_month"] == 12),
               1 if candidate["work_month"] == 12 else candidate["work_month"] + 1, 1)
    original_items: list[dict[str, Any]] = []
    total_updated = 0
    for table_name in ("time_reports", "payment_components"):
        items = conn.execute(f"""
            SELECT src.id, src.date, src.payment_year, src.payment_month, src.payment_note,
                   src.payment_marked_at, src.payment_marked_by
            FROM {table_name} src JOIN apartments ap ON ap.id=src.apartment_id
            WHERE src.person_id=%s
              AND (
                    (src.date>=%s AND src.date<%s)
                    OR (
                        src.date<%s
                        AND src.payment_year=%s AND src.payment_month=%s
                    )
                  )
              AND ap.housing_array_id=%s
            FOR UPDATE
        """, (
            candidate["person_id"], start, end, start,
            candidate["work_year"], candidate["work_month"],
            candidate["housing_array_id"],
        )).fetchall()
        eligible_ids = []
        for item in items:
            if source_item_belongs_to_candidate(
                item_date=item["date"],
                payment_year=item["payment_year"],
                payment_month=item["payment_month"],
                work_year=candidate["work_year"],
                work_month=candidate["work_month"],
            ) and is_item_eligible_for_rollforward(
                item["payment_year"], item["payment_month"],
                candidate["payment_year"], candidate["payment_month"],
            ):
                eligible_ids.append(item["id"])
                original_items.append({
                    "table": table_name,
                    "id": item["id"],
                    "payment_year": item["payment_year"],
                    "payment_month": item["payment_month"],
                    "payment_note": item["payment_note"],
                    "payment_marked_at": item["payment_marked_at"],
                    "payment_marked_by": item["payment_marked_by"],
                })
        if eligible_ids:
            updated = conn.execute(f"""
                UPDATE {table_name}
                SET payment_year=%s, payment_month=%s,
                    payment_note=CASE
                        WHEN COALESCE(payment_note, '') = '' THEN %s
                        WHEN payment_note LIKE %s THEN payment_note
                        ELSE payment_note || ' | ' || %s END,
                    payment_marked_at=NOW(), payment_marked_by=%s
                WHERE id=ANY(%s)
                RETURNING id
            """, (
                candidate["payment_year"], candidate["payment_month"],
                MISSING_FINAL_PAYMENT_NOTE,
                f"%{MISSING_FINAL_PAYMENT_NOTE}%",
                MISSING_FINAL_PAYMENT_NOTE,
                actor_person_id, eligible_ids,
            )).fetchall()
            total_updated += len(updated)
    if not total_updated:
        raise ValueError("לא נמצאו דיווחים מתאימים להעברה לחודש התשלום")
    periods_json = json.dumps(original_items, default=str, ensure_ascii=False)
    conn.execute("""
        UPDATE missing_gesher_candidates
        SET status='approved', approved_at=NOW(), approved_by=%s,
            rejected_at=NULL, rejected_by=NULL, original_payment_periods=%s::jsonb,
            updated_at=NOW()
        WHERE id=%s
    """, (actor_person_id, periods_json, candidate_id))
    candidate.update({"status": "approved", "updated_items": total_updated})
    return candidate


def mark_approved_candidates_in_export(
    conn,
    *,
    payment_year: int,
    payment_month: int,
    company_code: str,
    housing_array_id: Optional[int],
    export_file_id: int,
    content: str,
) -> int:
    """Lock approved candidates whose employee code is present in a generated file."""
    from services.gesher_difference import parse_gesher_file_lines
    ensure_missing_gesher_candidates_table(conn)

    employee_codes = sorted({
        row["employee_code"] for row in parse_gesher_file_lines(content)
    })
    if not employee_codes:
        return 0
    where = [
        "payment_year=%s", "payment_month=%s", "company_code=%s",
        "status='approved'", "employee_code=ANY(%s)",
    ]
    params: list[Any] = [payment_year, payment_month, company_code, employee_codes]
    if housing_array_id is not None:
        where.append("housing_array_id=%s")
        params.append(housing_array_id)
    rows = conn.execute(f"""
        UPDATE missing_gesher_candidates
        SET status='included_in_export', included_at=NOW(),
            included_export_file_id=%s, updated_at=NOW()
        WHERE {' AND '.join(where)}
        RETURNING id
    """, (export_file_id, *params)).fetchall()
    return len(rows)


def reopen_candidates_for_cancelled_export(conn, export_file_id: int) -> int:
    """Return candidates to approval when their generated export is cancelled."""
    ensure_missing_gesher_candidates_table(conn)
    rows = conn.execute("""
        UPDATE missing_gesher_candidates
        SET status='pending', included_at=NULL, included_export_file_id=NULL,
            approved_at=NULL, approved_by=NULL, updated_at=NOW()
        WHERE included_export_file_id=%s AND status='included_in_export'
        RETURNING id
    """, (export_file_id,)).fetchall()
    return len(rows)
