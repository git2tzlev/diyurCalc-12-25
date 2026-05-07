"""
Email service for DiyurCalc application.
Handles PDF generation and email sending for guide reports.
"""
from __future__ import annotations

import logging
import smtplib
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Optional, Dict, Any, List
import re
from core.config import config
from core.database import get_conn
from services.pdf_renderer import render_html_to_pdf_bytes

logger = logging.getLogger(__name__)
GENERIC_ERROR = "שגיאת מערכת. נסי שוב מאוחר יותר"


def get_email_settings(conn) -> Optional[Dict[str, Any]]:
    """Get email settings from database."""
    try:
        result = conn.execute("""
            SELECT id, smtp_host, smtp_port, smtp_user, smtp_password,
                   smtp_secure, from_email, from_name, is_active
            FROM email_settings
            WHERE is_active = TRUE
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if result:
            return dict(result)
        return None
    except Exception as e:
        logger.error(f"Error fetching email settings: {e}")
        return None


def save_email_settings(conn, settings: Dict[str, Any]) -> bool:
    """Save or update email settings in database."""
    try:
        # Check if settings exist
        existing = conn.execute("SELECT id FROM email_settings WHERE is_active = TRUE LIMIT 1").fetchone()

        # smtp_secure follows nodemailer convention: false = STARTTLS (587), true = SSL (465)
        smtp_secure = settings.get('smtp_secure', False)

        if existing:
            conn.execute("""
                UPDATE email_settings
                SET smtp_host = %s, smtp_port = %s, smtp_user = %s,
                    smtp_password = %s, from_email = %s, from_name = %s,
                    smtp_secure = %s, updated_at = NOW()
                WHERE id = %s
            """, (
                settings.get('smtp_host'),
                settings.get('smtp_port', 587),
                settings.get('smtp_user'),
                settings.get('smtp_password'),
                settings.get('from_email'),
                settings.get('from_name', 'דיור003'),
                smtp_secure,
                existing['id']
            ))
        else:
            conn.execute("""
                INSERT INTO email_settings
                (smtp_host, smtp_port, smtp_user, smtp_password, from_email, from_name, smtp_secure, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW())
            """, (
                settings.get('smtp_host'),
                settings.get('smtp_port', 587),
                settings.get('smtp_user'),
                settings.get('smtp_password'),
                settings.get('from_email'),
                settings.get('from_name', 'דיור003'),
                smtp_secure
            ))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving email settings: {e}")
        conn.rollback()
        return False


def test_email_connection(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Test SMTP connection with given settings."""
    try:
        smtp_host = settings.get('smtp_host')
        smtp_port = settings.get('smtp_port', 587)
        smtp_user = settings.get('smtp_user')
        smtp_password = settings.get('smtp_password')
        # smtp_secure follows nodemailer convention:
        # false = STARTTLS (port 587), true = SSL from start (port 465)
        smtp_secure = settings.get('smtp_secure', settings.get('use_tls', False))

        if not all([smtp_host, smtp_user, smtp_password]):
            return {"success": False, "error": "חסרים פרטי חיבור"}

        # Connect based on smtp_secure setting (nodemailer style)
        if smtp_secure:
            # SSL from start (typically port 465)
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
        else:
            # STARTTLS (typically port 587)
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.starttls()

        server.login(smtp_user, smtp_password)
        server.quit()

        return {"success": True, "message": "החיבור הצליח!"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "שגיאת אימות - בדוק שם משתמש וסיסמה"}
    except smtplib.SMTPConnectError:
        return {"success": False, "error": "לא ניתן להתחבר לשרת"}
    except Exception as e:
        logger.error(f"Error testing email connection: {e}", exc_info=True)
        return {"success": False, "error": GENERIC_ERROR}


def send_test_email(conn, to_email: str) -> Dict[str, Any]:
    """Send a test email to verify settings are working."""
    try:
        settings = get_email_settings(conn)
        if not settings:
            return {"success": False, "error": "הגדרות מייל לא נמצאו"}

        smtp_host = settings.get('smtp_host')
        smtp_port = settings.get('smtp_port', 587)
        smtp_user = settings.get('smtp_user')
        smtp_password = settings.get('smtp_password')
        from_email = settings.get('from_email')
        from_name = settings.get('from_name', 'דיור003')
        smtp_secure = settings.get('smtp_secure', False)

        if not all([smtp_host, smtp_user, smtp_password, from_email]):
            return {"success": False, "error": "חסרים פרטי הגדרות מייל"}

        # Create test message
        from email.header import Header
        from email.utils import formataddr

        msg = MIMEMultipart('alternative')
        # Encode Hebrew sender name properly
        msg['From'] = formataddr((str(Header(from_name, 'utf-8')), from_email))
        msg['To'] = to_email
        msg['Subject'] = Header("מייל בדיקה - דיור003", 'utf-8')

        # HTML body with RTL
        html_body = """<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
    <meta charset="utf-8">
</head>
<body style="direction: rtl; text-align: right; font-family: Arial, sans-serif;">
    <p>שלום,</p>
    <p>זהו מייל בדיקה ממערכת דיור003.<br>
    אם קיבלת הודעה זו, הגדרות המייל פועלות כראוי.</p>
    <p>בברכה,<br>
    מערכת דיור003</p>
</body>
</html>
"""
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # Connect and send
        if smtp_secure:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()

        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()

        return {"success": True, "message": f"מייל בדיקה נשלח בהצלחה ל-{to_email}"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "שגיאת אימות - בדוק שם משתמש וסיסמה"}
    except Exception as e:
        logger.error(f"Error sending test email: {e}")
        return {"success": False, "error": GENERIC_ERROR}


def generate_guide_pdf(person_id: int, year: int, month: int) -> Optional[bytes]:
    """יצירת PDF לדוח מדריך באמצעות רינדור ישיר של התבנית ו-Edge/Chrome headless."""
    from jinja2 import Environment, FileSystemLoader
    from routes.guide import prepare_guide_pdf_data

    try:
        # 1. הכנת נתונים - חיבור DB קצר, משתחרר לפני יצירת PDF
        with get_conn() as conn:
            pdf_data = prepare_guide_pdf_data(conn, person_id, year, month)
        if not pdf_data:
            raise ValueError(f"לא נמצאו נתונים למדריך {person_id}")

        # 2. רינדור ויצירת PDF - ללא חיבור DB
        env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
        template = env.get_template("guide_shifts_pdf.html")
        html_content = template.render(**pdf_data)

        logger.info(f"Generating PDF for person_id={person_id}")
        pdf_bytes = render_html_to_pdf_bytes(html_content)
        if not pdf_bytes:
            raise RuntimeError("קובץ PDF לא נוצר או ריק")
        logger.info(f"PDF generated successfully, size: {len(pdf_bytes)} bytes")
        return pdf_bytes
    except Exception as e:
        logger.error(f"Error generating guide PDF: {e}", exc_info=True)
        return None


def send_email_with_pdf(
    settings: Dict[str, Any],
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    pdf_bytes: bytes,
    pdf_filename: str
) -> Dict[str, Any]:
    """Send email with PDF attachment."""
    try:
        smtp_host = settings.get('smtp_host')
        smtp_port = settings.get('smtp_port', 587)
        smtp_user = settings.get('smtp_user')
        smtp_password = settings.get('smtp_password')
        from_email = settings.get('from_email')
        from_name = settings.get('from_name', 'דיור003')
        # smtp_secure follows nodemailer convention:
        # false = STARTTLS (port 587), true = SSL from start (port 465)
        smtp_secure = settings.get('smtp_secure', settings.get('use_tls', False))

        # Create message with proper Hebrew encoding
        from email.header import Header
        from email.utils import formataddr

        msg = MIMEMultipart()
        msg['From'] = formataddr((str(Header(from_name, 'utf-8')), from_email))
        msg['To'] = formataddr((str(Header(to_name, 'utf-8')), to_email))
        msg['Subject'] = Header(subject, 'utf-8')

        # Headers למניעת תשובות אוטומטיות ולסימון המייל כאוטומטי
        # Reply-To - אם יש כתובת noreply בהגדרות, נשתמש בה
        reply_to_email = settings.get('reply_to_email') or from_email
        msg['Reply-To'] = reply_to_email
        # RFC 3834 - סימון שהמייל נוצר אוטומטית
        msg['Auto-Submitted'] = 'auto-generated'
        # Microsoft Outlook - מניעת תשובות אוטומטיות
        msg['X-Auto-Response-Suppress'] = 'All'
        # סימון כמייל בכמות גדולה/אוטומטי
        msg['Precedence'] = 'bulk'

        # Add body as HTML with RTL for proper Hebrew display
        html_body = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="utf-8"></head>
<body style="direction: rtl; text-align: right; font-family: Arial, sans-serif;">
{body.replace(chr(10), '<br>')}
</body>
</html>
"""
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # Add PDF attachment
        pdf_attachment = MIMEApplication(pdf_bytes, _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=pdf_filename)
        msg.attach(pdf_attachment)

        # Connect based on smtp_secure setting (nodemailer style)
        if smtp_secure:
            # SSL from start (typically port 465)
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            # STARTTLS (typically port 587)
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()

        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()

        return {"success": True}
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return {"success": False, "error": GENERIC_ERROR}


def send_guide_email(person_id: int, year: int, month: int, custom_email: Optional[str] = None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """שליחת דוח מדריך במייל. מנהל חיבור DB בעצמו ומשחרר לפני עבודה כבדה."""
    try:
        # שליפת הגדרות ופרטי מדריך - חיבור DB קצר
        with get_conn() as conn:
            if not settings:
                settings = get_email_settings(conn)
            if not settings:
                return {"success": False, "error": "הגדרות מייל לא נמצאו. אנא הגדר אותן בעמוד ההגדרות."}

            person = conn.execute(
                "SELECT id, name, email FROM people WHERE id = %s",
                (person_id,)
            ).fetchone()

        if not person:
            return {"success": False, "error": "מדריך לא נמצא"}

        target_email = custom_email if custom_email else person['email']

        if not target_email:
            return {"success": False, "error": f"למדריך {person['name']} אין כתובת מייל"}

        # יצירת PDF - generate_guide_pdf מנהל חיבור DB בעצמו
        try:
            pdf_bytes = generate_guide_pdf(person_id, year, month)
        except Exception as pdf_err:
            logger.error(f"PDF generation failed for {person['name']}: {pdf_err}", exc_info=True)
            return {"success": False, "error": "שגיאה ביצירת PDF"}
        if not pdf_bytes:
            return {"success": False, "error": "שגיאה ביצירת PDF: קובץ ריק"}

        # Prepare email content
        subject = f"דוח פירוט שעות עבודה כנספח לתלוש השכר חודש {month:02d}/{year}"
        body = f"""שלום {person['name']},

מצורף דוח פירוט שעות העבודה והתשלום לחודש {month:02d}/{year}.

בברכה,
מדור שכר
צהר הלב

<span style="color: #888; font-size: 11px;">─────────────────────────────</span>
<span style="color: red; font-size: 11px;">הודעה זו נשלחה באופן אוטומטי. אין להשיב למייל זה.</span>
"""
        pdf_filename = f"דוח_שכר_{person['name']}_{month:02d}_{year}.pdf"

        # Send email
        result = send_email_with_pdf(
            settings=settings,
            to_email=target_email,
            to_name=person['name'],
            subject=subject,
            body=body,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename
        )

        if result['success']:
            return {"success": True, "message": f"המייל נשלח בהצלחה ל-{target_email}"}
        else:
            return result

    except Exception as e:
        logger.error(f"Error in send_guide_email: {e}", exc_info=True)
        return {"success": False, "error": GENERIC_ERROR}


def send_all_guides_email(
    year: int, month: int, housing_array_id: Optional[int] = None
) -> Dict[str, Any]:
    """שליחת דוחות לכל המדריכים הפעילים. משחרר חיבור DB לפני הלולאה."""
    try:
        # שליפת הגדרות ורשימת מדריכים - חיבור DB קצר
        with get_conn() as conn:
            settings = get_email_settings(conn)
            if not settings:
                return {"success": False, "error": "הגדרות מייל לא נמצאו"}

            if housing_array_id is not None:
                guides = conn.execute("""
                    SELECT DISTINCT p.id, p.name, p.email
                    FROM people p
                    JOIN time_reports tr ON tr.person_id = p.id
                    WHERE p.is_active = TRUE
                    AND p.email IS NOT NULL
                    AND p.email != ''
                    AND p.housing_array_id = %s
                    AND EXTRACT(YEAR FROM tr.date) = %s
                    AND EXTRACT(MONTH FROM tr.date) = %s
                """, (housing_array_id, year, month)).fetchall()
            else:
                guides = conn.execute("""
                    SELECT DISTINCT p.id, p.name, p.email
                    FROM people p
                    JOIN time_reports tr ON tr.person_id = p.id
                    WHERE p.is_active = TRUE
                    AND p.email IS NOT NULL
                    AND p.email != ''
                    AND EXTRACT(YEAR FROM tr.date) = %s
                    AND EXTRACT(MONTH FROM tr.date) = %s
                """, (year, month)).fetchall()

        if not guides:
            return {"success": False, "error": "לא נמצאו מדריכים פעילים עם מייל לחודש זה"}

        results = {"success": [], "failed": []}

        for guide in guides:
            result = send_guide_email(guide['id'], year, month, settings=settings)
            if result.get('success'):
                results['success'].append(guide['name'])
            else:
                results['failed'].append({
                    "name": guide['name'],
                    "error": result.get('error', 'שגיאה לא ידועה')
                })

        total = len(guides)
        success_count = len(results['success'])

        return {
            "success": True,
            "message": f"נשלחו {success_count} מתוך {total} מיילים",
            "details": results
        }

    except Exception as e:
        logger.error(f"Error in send_all_guides_email: {e}", exc_info=True)
        return {"success": False, "error": GENERIC_ERROR}


def send_all_guides_to_single_email(
    year: int,
    month: int,
    target_email: str,
    housing_array_id: Optional[int] = None,
) -> Dict[str, Any]:
    """שליחת כל דוחות המדריכים למייל אחד (קובץ PDF אחד משולב)."""
    try:
        if not target_email:
            return {"success": False, "error": "לא הוזנה כתובת מייל"}

        # שליפת הגדרות ורשימת מדריכים - חיבור DB קצר
        with get_conn() as conn:
            settings = get_email_settings(conn)
            if not settings:
                return {"success": False, "error": "הגדרות מייל לא נמצאו"}

            if housing_array_id is not None:
                guides = conn.execute("""
                    SELECT DISTINCT p.id, p.name
                    FROM people p
                    JOIN time_reports tr ON tr.person_id = p.id
                    WHERE p.is_active = TRUE
                    AND p.housing_array_id = %s
                    AND EXTRACT(YEAR FROM tr.date) = %s
                    AND EXTRACT(MONTH FROM tr.date) = %s
                    ORDER BY p.name
                """, (housing_array_id, year, month)).fetchall()
            else:
                guides = conn.execute("""
                    SELECT DISTINCT p.id, p.name
                    FROM people p
                    JOIN time_reports tr ON tr.person_id = p.id
                    WHERE p.is_active = TRUE
                    AND EXTRACT(YEAR FROM tr.date) = %s
                    AND EXTRACT(MONTH FROM tr.date) = %s
                    ORDER BY p.name
                """, (year, month)).fetchall()

        if not guides:
            return {"success": False, "error": "לא נמצאו מדריכים עם משמרות בחודש זה"}

        # יצירת PDF משולב - _generate_combined_guides_pdf מנהל חיבורים בעצמו
        pdf_bytes, guide_count, failed_guides = _generate_combined_guides_pdf(
            guides, year, month
        )

        if not pdf_bytes:
            return {"success": False, "error": "לא ניתן היה ליצור את ה-PDF"}

        # Send single email with combined PDF
        result = send_email_with_pdf(
            settings=settings,
            to_email=target_email,
            to_name="",
            subject=f"דוחות שכר כל המדריכים - חודש {month:02d}/{year}",
            body=f"""שלום,

מצורף קובץ PDF עם דוחות פירוט שעות העבודה והתשלום לכל המדריכים לחודש {month:02d}/{year}.

סה"כ {guide_count} דוחות בקובץ (כל מדריך בעמוד נפרד).
{f"לא ניתן היה ליצור דוח עבור: {', '.join(failed_guides)}" if failed_guides else ""}

<span style="color: #888; font-size: 11px;">─────────────────────────────</span>
<span style="color: red; font-size: 11px;">הודעה זו נשלחה באופן אוטומטי. אין להשיב למייל זה.</span>
""",
            pdf_bytes=pdf_bytes,
            pdf_filename=f"דוחות_שכר_כל_המדריכים_{month:02d}_{year}.pdf"
        )

        if result['success']:
            msg = f"נשלח קובץ PDF עם {guide_count} דוחות ל-{target_email}"
            if failed_guides:
                msg += f" (נכשלו: {', '.join(failed_guides)})"
            return {"success": True, "message": msg}
        else:
            return result

    except Exception as e:
        logger.error(f"Error in send_all_guides_to_single_email: {e}", exc_info=True)
        return {"success": False, "error": GENERIC_ERROR}


def send_selected_guides_email(
    guide_ids: list,
    year: int,
    month: int,
    housing_array_id: Optional[int] = None,
) -> Dict[str, Any]:
    """שליחת דוחות למדריכים נבחרים בלבד (לפי רשימת מזהים)."""
    try:
        logger.info(f"=== התחלת שליחת מיילים ל-{len(guide_ids)} מדריכים - {month:02d}/{year} ===")

        if not guide_ids:
            logger.warning("לא נבחרו מדריכים")
            return {"success": False, "error": "לא נבחרו מדריכים"}

        # שליפת הגדרות ורשימת מדריכים - חיבור DB קצר
        with get_conn() as conn:
            settings = get_email_settings(conn)
            if not settings:
                logger.error("הגדרות מייל לא נמצאו")
                return {"success": False, "error": "הגדרות מייל לא נמצאו"}

            placeholders = ",".join(["%s"] * len(guide_ids))
            if housing_array_id is not None:
                params = tuple(guide_ids) + (housing_array_id,)
                guides = conn.execute(f"""
                    SELECT id, name, email
                    FROM people
                    WHERE id IN ({placeholders})
                    AND housing_array_id = %s
                    AND email IS NOT NULL
                    AND email != ''
                """, params).fetchall()
            else:
                guides = conn.execute(f"""
                    SELECT id, name, email
                    FROM people
                    WHERE id IN ({placeholders})
                    AND email IS NOT NULL
                    AND email != ''
                """, tuple(guide_ids)).fetchall()

        if not guides:
            logger.warning("לא נמצאו מדריכים עם מייל ברשימה")
            return {"success": False, "error": "לא נמצאו מדריכים עם מייל ברשימה"}

        logger.info(f"נמצאו {len(guides)} מדריכים עם מייל מתוך {len(guide_ids)} שנבחרו")

        results = {"success": [], "failed": []}
        total = len(guides)

        for idx, guide in enumerate(guides, 1):
            guide_name = guide['name']
            guide_email = guide['email']
            logger.info(f"[{idx}/{total}] שולח ל: {guide_name} ({guide_email})...")

            result = send_guide_email(guide['id'], year, month, settings=settings)

            if result.get('success'):
                logger.info(f"[{idx}/{total}] נשלח בהצלחה: {guide_name} -> {guide_email}")
                results['success'].append({"name": guide_name, "email": guide_email})
            else:
                error_msg = result.get('error', 'שגיאה לא ידועה')
                logger.error(f"[{idx}/{total}] נכשל: {guide_name} ({guide_email}) - {error_msg}")
                results['failed'].append({
                    "name": guide_name,
                    "email": guide_email,
                    "error": error_msg
                })

        success_count = len(results['success'])
        failed_count = len(results['failed'])

        logger.info(f"=== סיום שליחה: {success_count} הצליחו, {failed_count} נכשלו ===")

        if failed_count > 0:
            logger.warning(f"נכשלו: {', '.join([f['name'] for f in results['failed']])}")

        return {
            "success": True,
            "message": f"נשלחו {success_count} מתוך {total} מיילים",
            "total": total,
            "success_count": success_count,
            "failed_count": failed_count,
            "details": results
        }

    except Exception as e:
        logger.error(f"Error in send_selected_guides_email: {e}", exc_info=True)
        return {"success": False, "error": GENERIC_ERROR}


def send_selected_guides_to_single_email(
    guide_ids: list,
    year: int,
    month: int,
    target_email: str,
    housing_array_id: Optional[int] = None,
) -> Dict[str, Any]:
    """שליחת דוחות מדריכים נבחרים למייל אחד (קובץ PDF משולב)."""
    try:
        if not target_email:
            return {"success": False, "error": "לא הוזנה כתובת מייל"}

        if not guide_ids:
            return {"success": False, "error": "לא נבחרו מדריכים"}

        # שליפת הגדרות ורשימת מדריכים - חיבור DB קצר
        with get_conn() as conn:
            settings = get_email_settings(conn)
            if not settings:
                return {"success": False, "error": "הגדרות מייל לא נמצאו"}

            placeholders = ",".join(["%s"] * len(guide_ids))
            if housing_array_id is not None:
                params = tuple(guide_ids) + (housing_array_id,)
                guides = conn.execute(f"""
                    SELECT id, name
                    FROM people
                    WHERE id IN ({placeholders})
                    AND housing_array_id = %s
                    ORDER BY name
                """, params).fetchall()
            else:
                guides = conn.execute(f"""
                    SELECT id, name
                    FROM people
                    WHERE id IN ({placeholders})
                    ORDER BY name
                """, tuple(guide_ids)).fetchall()

        if not guides:
            return {"success": False, "error": "לא נמצאו מדריכים ברשימה"}

        # יצירת PDF משולב - _generate_combined_guides_pdf מנהל חיבורים בעצמו
        pdf_bytes, guide_count, failed_guides = _generate_combined_guides_pdf(
            guides, year, month
        )

        if not pdf_bytes:
            return {"success": False, "error": "לא ניתן היה ליצור את ה-PDF"}

        # Send single email with combined PDF
        result = send_email_with_pdf(
            settings=settings,
            to_email=target_email,
            to_name="",
            subject=f"דוחות שכר מדריכים - חודש {month:02d}/{year}",
            body=f"""שלום,

מצורף קובץ PDF עם דוחות פירוט שעות העבודה והתשלום לחודש {month:02d}/{year}.

סה"כ {guide_count} דוחות בקובץ (כל מדריך בעמוד נפרד).
{f"לא ניתן היה ליצור דוח עבור: {', '.join(failed_guides)}" if failed_guides else ""}

<span style="color: #888; font-size: 11px;">─────────────────────────────</span>
<span style="color: red; font-size: 11px;">הודעה זו נשלחה באופן אוטומטי. אין להשיב למייל זה.</span>
""",
            pdf_bytes=pdf_bytes,
            pdf_filename=f"דוחות_שכר_מדריכים_{month:02d}_{year}.pdf"
        )

        if result['success']:
            msg = f"נשלח קובץ PDF עם {guide_count} דוחות ל-{target_email}"
            if failed_guides:
                msg += f" (נכשלו: {', '.join(failed_guides)})"
            return {"success": True, "message": msg}
        else:
            return result

    except Exception as e:
        logger.error(f"Error in send_selected_guides_to_single_email: {e}", exc_info=True)
        return {"success": False, "error": GENERIC_ERROR}


def _generate_combined_guides_pdf(guides, year: int, month: int):
    """יצירת קובץ PDF אחד משולב עם כל המדריכים - כל מדריך בעמוד נפרד.

    משתמש ב-prepare_guide_pdf_data מ-routes.guide לקבלת נתונים זהים לדוח הבודד.
    כל מדריך מקבל חיבור DB קצר ומשחרר אותו לפני יצירת PDF.
    """
    from jinja2 import Environment, FileSystemLoader
    from routes.guide import prepare_guide_pdf_data

    failed_guides = []
    successful_guides = 0

    try:
        env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
        template = env.get_template("guide_shifts_pdf.html")

        html_parts = []

        for guide in guides:
            try:
                person_id = guide['id']
                person_name = guide['name']

                # חיבור DB קצר לכל מדריך - משתחרר מיד
                with get_conn() as conn:
                    pdf_data = prepare_guide_pdf_data(conn, person_id, year, month)

                if not pdf_data:
                    failed_guides.append(person_name)
                    continue

                # Render template for this guide
                guide_html = template.render(**pdf_data)

                # Extract body content
                body_match = re.search(r'<body[^>]*>(.*?)</body>', guide_html, re.DOTALL | re.IGNORECASE)
                body_content = body_match.group(1) if body_match else guide_html

                # Add page break using CSS class
                html_parts.append(f'<div class="guide-page">{body_content}</div>')
                successful_guides += 1
                logger.info(f"Generated HTML for guide: {person_name}")

            except Exception as e:
                logger.warning(f"Error generating HTML for guide {guide['name']}: {e}", exc_info=True)
                failed_guides.append(guide['name'])

        if not html_parts:
            return None, 0, failed_guides

        # Build combined HTML document with exact same CSS as individual reports
        combined_html = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
    <meta charset="UTF-8">
    <title>דוחות שכר כל המדריכים - {month:02d}/{year}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: Arial, sans-serif;
            direction: rtl;
            padding: 30px 40px;
            background: #fff;
            color: #333;
            font-size: 12px;
        }}
        .header {{
            text-align: center;
            margin-bottom: 25px;
        }}
        .header h1 {{
            color: #1e3a5f;
            font-size: 24px;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        .header .worker-name {{
            font-size: 14px;
            color: #333;
            margin-bottom: 5px;
        }}
        .header .period {{
            font-size: 13px;
            color: #555;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }}
        th {{
            background: #f5f5f5;
            font-weight: bold;
            padding: 8px 6px;
            border: 1px solid #ddd;
            text-align: center;
            font-size: 11px;
        }}
        td {{
            padding: 6px;
            border: 1px solid #ddd;
            text-align: center;
            font-size: 11px;
        }}
        tr:nth-child(even) {{
            background: #fafafa;
        }}
        .travel-row {{
            background: #e8f5e9 !important;
        }}
        .travel-row td {{
            font-weight: 500;
        }}
        .tagbor-group:not(.tagbor-last) td {{
            border-bottom: none;
        }}
        .summary-table {{
            width: 100%;
            margin-top: 15px;
            border: none;
        }}
        .summary-table td {{
            border: none;
            padding: 8px 15px;
            text-align: center;
            font-size: 13px;
        }}
        .summary-label {{
            font-weight: bold;
            color: #333;
        }}
        .summary-value {{
            font-weight: bold;
            color: #1e3a5f;
        }}
        .footer {{
            text-align: center;
            margin-top: 30px;
            padding-top: 15px;
            border-top: 1px solid #eee;
            font-size: 10px;
            color: #888;
        }}
        .guide-page {{
            page-break-after: always;
        }}
        .guide-page:last-child {{
            page-break-after: auto;
        }}
        @media print {{
            body {{
                padding: 15px 20px;
            }}
            .header h1 {{
                font-size: 20pt;
            }}
        }}
    </style>
</head>
<body>
{''.join(html_parts)}
</body>
</html>
"""

        logger.info(f"Generating combined PDF for {successful_guides} guides using headless browser")

        pdf_bytes = render_html_to_pdf_bytes(
            combined_html,
            timeout_seconds=120,
            settle_seconds=1.0,
        )
        if not pdf_bytes:
            logger.error("Combined PDF file was not created or is empty")
            return None, 0, failed_guides

        logger.info(f"Combined PDF generated successfully, size: {len(pdf_bytes)} bytes")
        return pdf_bytes, successful_guides, failed_guides

    except Exception as e:
        logger.error(f"Error generating combined PDF: {e}", exc_info=True)
        return None, 0, failed_guides


# ─── Email Logs ───────────────────────────────────────────────


def ensure_email_logs_table(conn) -> None:
    """יצירת טבלת email_logs אם לא קיימת."""
    sql = """
        CREATE TABLE IF NOT EXISTS email_logs (
            id SERIAL PRIMARY KEY,
            recipient_id INTEGER,
            recipient_email VARCHAR(255),
            recipient_name VARCHAR(255),
            email_type VARCHAR(50) NOT NULL,
            subject VARCHAR(500),
            status VARCHAR(20) NOT NULL,
            error_message TEXT,
            month INTEGER,
            year INTEGER,
            sent_by INTEGER,
            sent_at TIMESTAMP DEFAULT NOW(),
            batch_id VARCHAR(100)
        )
    """
    if hasattr(conn, "execute"):
        conn.execute(sql)
    else:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()
    conn.commit()


def insert_email_log(
    conn,
    *,
    recipient_id: Optional[int] = None,
    recipient_email: str = "",
    recipient_name: str = "",
    email_type: str = "shifts_report",
    subject: str = "",
    status: str = "sent",
    error_message: Optional[str] = None,
    month: Optional[int] = None,
    year: Optional[int] = None,
    sent_by: Optional[int] = None,
    batch_id: Optional[str] = None,
) -> None:
    """הוספת רשומת לוג שליחת מייל."""
    try:
        conn.execute("""
            INSERT INTO email_logs
            (recipient_id, recipient_email, recipient_name, email_type,
             subject, status, error_message, month, year, sent_by, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            recipient_id, recipient_email, recipient_name, email_type,
            subject, status, error_message, month, year, sent_by, batch_id,
        ))
        conn.commit()
    except Exception as e:
        logger.error(f"Error inserting email log: {e}")
        try:
            conn.rollback()
            ensure_email_logs_table(conn)
            conn.execute("""
                INSERT INTO email_logs
                (recipient_id, recipient_email, recipient_name, email_type,
                 subject, status, error_message, month, year, sent_by, batch_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                recipient_id, recipient_email, recipient_name, email_type,
                subject, status, error_message, month, year, sent_by, batch_id,
            ))
            conn.commit()
        except Exception as e2:
            logger.error(f"Error inserting email log after table creation: {e2}")


def get_email_logs(
    conn,
    *,
    batch_id: Optional[str] = None,
    recipient_id: Optional[int] = None,
    month: Optional[int] = None,
    year: Optional[int] = None,
    status: Optional[str] = None,
    housing_array_id: Optional[int] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """שליפת לוגי שליחת מייל עם סינון."""
    try:
        conditions = []
        params: list = []

        if batch_id:
            conditions.append("batch_id = %s")
            params.append(batch_id)
        if recipient_id:
            conditions.append("recipient_id = %s")
            params.append(recipient_id)
        if month:
            conditions.append("month = %s")
            params.append(month)
        if year:
            conditions.append("year = %s")
            params.append(year)
        if status:
            conditions.append("status = %s")
            params.append(status)
        if housing_array_id is not None:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM people p
                    WHERE p.id = email_logs.recipient_id
                      AND p.housing_array_id = %s
                )
                """
            )
            params.append(housing_array_id)

        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        params.append(limit)

        rows = conn.execute(f"""
            SELECT id, recipient_id, recipient_email, recipient_name,
                   email_type, subject, status, error_message,
                   month, year, sent_by, sent_at, batch_id
            FROM email_logs
            WHERE {where_clause}
            ORDER BY sent_at DESC
            LIMIT %s
        """, tuple(params)).fetchall()

        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching email logs: {e}")
        return []


def get_batch_summary(conn, batch_id: str, housing_array_id: Optional[int] = None) -> Dict[str, Any]:
    """סיכום batch שליחה לפי batch_id."""
    try:
        params: list[Any] = [batch_id]
        housing_filter = ""
        if housing_array_id is not None:
            housing_filter = """
              AND EXISTS (
                  SELECT 1
                  FROM people p
                  WHERE p.id = email_logs.recipient_id
                    AND p.housing_array_id = %s
              )
            """
            params.append(housing_array_id)

        rows = conn.execute(f"""
            SELECT status, COUNT(*) as count
            FROM email_logs
            WHERE batch_id = %s
            {housing_filter}
            GROUP BY status
        """, tuple(params)).fetchall()

        summary = {"sent": 0, "failed": 0, "skipped": 0, "total": 0}
        for row in rows:
            summary[row["status"]] = row["count"]
            summary["total"] += row["count"]
        return summary
    except Exception as e:
        logger.error(f"Error fetching batch summary: {e}")
        return {"sent": 0, "failed": 0, "skipped": 0, "total": 0}


def generate_batch_id() -> str:
    """יצירת מזהה ייחודי ל-batch שליחה."""
    return f"bulk-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def _is_valid_email(email: Optional[str]) -> bool:
    """בדיקת תקינות בסיסית של כתובת מייל."""
    if not email:
        return False
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))


def process_guide_for_bulk(
    guide: Dict[str, Any],
    year: int,
    month: int,
    batch_id: str,
    settings: Dict[str, Any],
    sent_by: Optional[int] = None,
) -> Dict[str, Any]:
    """עיבוד מדריך בודד בשליחה מרוכזת - שליחה + לוג."""
    guide_id = guide["id"]
    guide_name = guide["name"]
    guide_email = guide.get("email", "")

    subject = f"דוח פירוט שעות עבודה כנספח לתלוש השכר חודש {month:02d}/{year}"

    if not _is_valid_email(guide_email):
        with get_conn() as conn:
            insert_email_log(
                conn,
                recipient_id=guide_id,
                recipient_email=guide_email,
                recipient_name=guide_name,
                status="skipped",
                error_message="אין מייל תקין",
                month=month, year=year,
                sent_by=sent_by,
                batch_id=batch_id,
                subject=subject,
            )
        return {
            "success": False,
            "id": guide_id,
            "name": guide_name,
            "status": "skipped",
            "reason": "אין מייל תקין",
        }

    try:
        result = send_guide_email(guide_id, year, month, guide_email, settings)

        status = "sent" if result.get("success") else "failed"
        error_msg = None if result.get("success") else result.get("error", "שגיאה לא ידועה")

        with get_conn() as conn:
            insert_email_log(
                conn,
                recipient_id=guide_id,
                recipient_email=guide_email,
                recipient_name=guide_name,
                status=status,
                error_message=error_msg,
                month=month, year=year,
                sent_by=sent_by,
                batch_id=batch_id,
                subject=subject,
            )

        return {
            "success": result.get("success", False),
            "id": guide_id,
            "name": guide_name,
            "email": guide_email,
            "status": status,
            "reason": error_msg,
        }

    except Exception as e:
        logger.error(f"Error processing guide {guide_name}: {e}", exc_info=True)
        error_msg = "שגיאה בשליחה"
        with get_conn() as conn:
            insert_email_log(
                conn,
                recipient_id=guide_id,
                recipient_email=guide_email,
                recipient_name=guide_name,
                status="failed",
                error_message=error_msg,
                month=month, year=year,
                sent_by=sent_by,
                batch_id=batch_id,
                subject=subject,
            )
        return {
            "success": False,
            "id": guide_id,
            "name": guide_name,
            "status": "failed",
            "reason": "שגיאה בשליחה",
        }
