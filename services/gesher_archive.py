"""Archive generated Gesher export files."""
from __future__ import annotations

from typing import Any, Optional


def ensure_gesher_export_files_table(conn) -> None:
    """Create the Gesher export archive table if it does not exist."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gesher_export_files (
                id SERIAL PRIMARY KEY,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                company_code TEXT NULL,
                housing_array_id INTEGER NULL REFERENCES housing_arrays(id),
                export_scope TEXT NOT NULL,
                person_ids INTEGER[] NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                encoding TEXT NOT NULL DEFAULT 'ascii',
                line_count INTEGER NOT NULL DEFAULT 0,
                notes TEXT NULL,
                is_cancelled BOOLEAN NOT NULL DEFAULT false,
                cancelled_at TIMESTAMP NULL,
                cancelled_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
                is_final BOOLEAN NOT NULL DEFAULT false,
                finalized_at TIMESTAMP NULL,
                finalized_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                created_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
                notes_updated_at TIMESTAMP NULL,
                notes_updated_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            ALTER TABLE gesher_export_files
            ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN NOT NULL DEFAULT false
        """)
        cursor.execute("""
            ALTER TABLE gesher_export_files
            ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP NULL
        """)
        cursor.execute("""
            ALTER TABLE gesher_export_files
            ADD COLUMN IF NOT EXISTS cancelled_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
        """)
        cursor.execute("""
            ALTER TABLE gesher_export_files
            ADD COLUMN IF NOT EXISTS is_final BOOLEAN NOT NULL DEFAULT false
        """)
        cursor.execute("""
            ALTER TABLE gesher_export_files
            ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMP NULL
        """)
        cursor.execute("""
            ALTER TABLE gesher_export_files
            ADD COLUMN IF NOT EXISTS finalized_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gesher_export_files_period
            ON gesher_export_files (year, month)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gesher_export_files_housing
            ON gesher_export_files (housing_array_id)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def count_gesher_data_lines(content: str) -> int:
    """Count non-empty data lines in a Gesher file, excluding the header."""
    lines = [line for line in (content or "").splitlines() if line.strip()]
    return max(len(lines) - 1, 0)


def save_gesher_export_file(
    conn,
    *,
    year: int,
    month: int,
    company_code: Optional[str],
    housing_array_id: Optional[int],
    export_scope: str,
    filename: str,
    content: str,
    encoding: str,
    created_by: Optional[int],
    person_ids: Optional[list[int]] = None,
) -> Optional[int]:
    """Save an exported Gesher file and return its archive id."""
    if not content:
        return None

    ensure_gesher_export_files_table(conn.conn if hasattr(conn, "conn") else conn)
    row = conn.execute("""
        INSERT INTO gesher_export_files
            (year, month, company_code, housing_array_id, export_scope, person_ids,
             filename, content, encoding, line_count, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        year,
        month,
        company_code,
        housing_array_id,
        export_scope,
        person_ids,
        filename,
        content,
        encoding,
        count_gesher_data_lines(content),
        created_by,
    )).fetchone()
    return row["id"] if row else None


def list_gesher_export_files(
    conn,
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
    company_code: Optional[str] = None,
    housing_array_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """List archived Gesher files, scoped by housing array when provided."""
    ensure_gesher_export_files_table(conn.conn if hasattr(conn, "conn") else conn)
    where = []
    params: list[Any] = []

    if year is not None:
        where.append("gef.year = %s")
        params.append(year)
    if month is not None:
        where.append("gef.month = %s")
        params.append(month)
    if company_code:
        where.append("gef.company_code = %s")
        params.append(company_code)
    if housing_array_id is not None:
        where.append("gef.housing_array_id = %s")
        params.append(housing_array_id)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(f"""
        SELECT gef.id, gef.year, gef.month, gef.company_code, gef.housing_array_id,
               ha.name AS housing_array_name,
               gef.export_scope, gef.filename, gef.encoding, gef.line_count,
               gef.notes, gef.is_cancelled, gef.cancelled_at,
               gef.is_final, gef.finalized_at, gef.created_at,
               creator.name AS created_by_name,
               note_editor.name AS notes_updated_by_name,
               gef.notes_updated_at,
               canceller.name AS cancelled_by_name,
               finalizer.name AS finalized_by_name
        FROM gesher_export_files gef
        LEFT JOIN housing_arrays ha ON ha.id = gef.housing_array_id
        LEFT JOIN people creator ON creator.id = gef.created_by
        LEFT JOIN people note_editor ON note_editor.id = gef.notes_updated_by
        LEFT JOIN people canceller ON canceller.id = gef.cancelled_by
        LEFT JOIN people finalizer ON finalizer.id = gef.finalized_by
        {where_sql}
        ORDER BY gef.created_at DESC, gef.id DESC
        LIMIT 300
    """, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def get_gesher_export_file(
    conn,
    file_id: int,
    *,
    housing_array_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Fetch one archived Gesher file, respecting optional housing scope."""
    ensure_gesher_export_files_table(conn.conn if hasattr(conn, "conn") else conn)
    if housing_array_id is not None:
        row = conn.execute("""
            SELECT gef.*, ha.name AS housing_array_name, p.name AS created_by_name
            FROM gesher_export_files gef
            LEFT JOIN housing_arrays ha ON ha.id = gef.housing_array_id
            LEFT JOIN people p ON p.id = gef.created_by
            WHERE gef.id = %s AND gef.housing_array_id = %s
        """, (file_id, housing_array_id)).fetchone()
    else:
        row = conn.execute("""
            SELECT gef.*, ha.name AS housing_array_name, p.name AS created_by_name
            FROM gesher_export_files gef
            LEFT JOIN housing_arrays ha ON ha.id = gef.housing_array_id
            LEFT JOIN people p ON p.id = gef.created_by
            WHERE gef.id = %s
        """, (file_id,)).fetchone()
    return dict(row) if row else None


def update_gesher_export_note(
    conn,
    file_id: int,
    notes: str,
    *,
    updated_by: Optional[int],
    housing_array_id: Optional[int] = None,
) -> bool:
    """Update an archived Gesher file note."""
    ensure_gesher_export_files_table(conn.conn if hasattr(conn, "conn") else conn)
    if housing_array_id is not None:
        row = conn.execute("""
            UPDATE gesher_export_files
            SET notes = %s, notes_updated_at = NOW(), notes_updated_by = %s
            WHERE id = %s AND housing_array_id = %s
            RETURNING id
        """, (notes, updated_by, file_id, housing_array_id)).fetchone()
    else:
        row = conn.execute("""
            UPDATE gesher_export_files
            SET notes = %s, notes_updated_at = NOW(), notes_updated_by = %s
            WHERE id = %s
            RETURNING id
        """, (notes, updated_by, file_id)).fetchone()
    return bool(row)


def set_gesher_export_status(
    conn,
    file_id: int,
    status: str,
    *,
    updated_by: Optional[int],
    housing_array_id: Optional[int] = None,
) -> bool:
    """Set archive file status: draft, final, or cancelled."""
    ensure_gesher_export_files_table(conn.conn if hasattr(conn, "conn") else conn)
    if status == "cancelled":
        set_sql = """
            is_cancelled = true,
            cancelled_at = NOW(),
            cancelled_by = %s,
            is_final = false,
            finalized_at = NULL,
            finalized_by = NULL
        """
        params: list[Any] = [updated_by]
    elif status == "final":
        set_sql = """
            is_final = true,
            finalized_at = NOW(),
            finalized_by = %s,
            is_cancelled = false,
            cancelled_at = NULL,
            cancelled_by = NULL
        """
        params = [updated_by]
    elif status == "draft":
        set_sql = """
            is_final = false,
            finalized_at = NULL,
            finalized_by = NULL,
            is_cancelled = false,
            cancelled_at = NULL,
            cancelled_by = NULL
        """
        params = []
    else:
        raise ValueError("סטטוס קובץ גשר לא תקין")

    if housing_array_id is not None:
        row = conn.execute(f"""
            UPDATE gesher_export_files
            SET {set_sql}
            WHERE id = %s AND housing_array_id = %s
            RETURNING id
        """, tuple(params + [file_id, housing_array_id])).fetchone()
    else:
        row = conn.execute(f"""
            UPDATE gesher_export_files
            SET {set_sql}
            WHERE id = %s
            RETURNING id
        """, tuple(params + [file_id])).fetchone()
    return bool(row)
