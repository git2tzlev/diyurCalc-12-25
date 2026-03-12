#!/usr/bin/env python3
"""בדיקת עמודים שלא הותאמו ואימות חילוץ נתונים."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from core.database import get_conn


def extract_employee_number(page) -> int | None:
    """חילוץ מספר עובד מעמוד."""
    blocks = page.get_text('dict')
    for block in blocks['blocks']:
        if 'lines' not in block:
            continue
        for line in block['lines']:
            text = ''
            for span in line['spans']:
                text += span['text']
            text = text.strip()
            bbox = line['bbox']
            if bbox[1] < 25 and bbox[0] > 540 and text.isdigit() and len(text) >= 4:
                return int(text)
    return None


def extract_payment_codes(page) -> dict:
    """חילוץ קודי תשלום מעמוד."""
    blocks = page.get_text('dict')
    codes = {}
    for block in blocks['blocks']:
        if 'lines' not in block:
            continue
        for line in block['lines']:
            text = ''
            for span in line['spans']:
                text += span['text']
            text = text.strip()
            bbox = line['bbox']
            if 410 < bbox[0] < 420 and 140 < bbox[1] < 280:
                if text.replace('-', '').isdigit() and text != '---':
                    codes[round(bbox[1], 0)] = text
    return codes


pdf_path = Path(__file__).resolve().parent.parent / "תלושים.pdf"
doc = fitz.open(str(pdf_path))

# בדיקה לכל העמודים
all_pages = []
for i in range(len(doc)):
    emp = extract_employee_number(doc[i])
    codes = extract_payment_codes(doc[i])
    all_pages.append({'page': i + 1, 'emp': emp, 'codes': list(codes.values())})

# חיבור ל-DB לבדיקת meirav_code
with get_conn() as conn:
    guides = conn.execute("""
        SELECT DISTINCT p.id, p.name, p.meirav_code
        FROM people p
        JOIN time_reports tr ON tr.person_id = p.id
        WHERE tr.date >= '2025-11-01' AND tr.date < '2025-12-01'
        ORDER BY p.name
    """).fetchall()

    meirav_to_name = {g["meirav_code"]: g["name"] for g in guides if g["meirav_code"]}

    # עמודים ללא מספר עובד
    print("=== Pages without employee number ===")
    for p in all_pages:
        if p['emp'] is None:
            print(f"  Page {p['page']}: no emp number, codes={p['codes']}")

    # עמודים שלא נמצא להם מדריך במערכת
    matched_emps = set()
    print("\n=== Pages with employee number but no match in system ===")
    for p in all_pages:
        if p['emp'] and p['emp'] not in meirav_to_name:
            print(f"  Page {p['page']}: emp={p['emp']}, codes={p['codes']}")
        if p['emp']:
            matched_emps.add(p['emp'])

    # מדריכים שיש להם meirav_code אבל לא נמצאו ב-PDF
    print("\n=== System guides not found in PDF ===")
    for g in guides:
        mc = g["meirav_code"]
        if mc and mc not in matched_emps:
            print(f"  {g['name']} (meirav_code={mc})")
        elif not mc:
            print(f"  {g['name']} (NO meirav_code)")

    # סיכום
    pdf_emps = {p['emp'] for p in all_pages if p['emp']}
    print(f"\n=== Summary ===")
    print(f"PDF pages: {len(doc)}")
    print(f"Pages with emp number: {len(pdf_emps)}")
    print(f"System guides: {len(guides)}")
    print(f"System guides with meirav_code: {sum(1 for g in guides if g['meirav_code'])}")
    print(f"Matched: {len(pdf_emps & set(meirav_to_name.keys()))}")

    # אימות נקודתי - הצגת ערכים של 3 מדריכים ידועים
    print("\n=== Spot check: page 2 (expected emp 7381 = Atias Esther) ===")
    p2 = all_pages[1]
    print(f"  emp={p2['emp']}, codes={p2['codes']}")
    if p2['emp'] in meirav_to_name:
        print(f"  Matched to: {meirav_to_name[p2['emp']]}")
