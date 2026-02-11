"""
Email service for DiyurCalc application.
Handles PDF generation and email sending for guide reports.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Optional, Dict, Any
import os
import re
from core.config import config
from core.database import get_conn

logger = logging.getLogger(__name__)


def safe_delete_file(file_path: str, max_retries: int = 5, retry_delay: float = 1.0, initial_wait: float = 2.0) -> bool:
    """
    Safely delete a file with retry mechanism for Windows file locking issues.
    
    Args:
        file_path: Path to the file to delete
        max_retries: Maximum number of retry attempts (default: 5)
        retry_delay: Delay between retries in seconds (default: 1.0)
        initial_wait: Initial wait time before first deletion attempt in seconds (default: 2.0)
    
    Returns:
        True if file was successfully deleted, False otherwise
    """
    import time
    
    if not os.path.exists(file_path):
        logger.debug(f"File does not exist, nothing to delete: {file_path}")
        return True
    
    # Initial wait to allow processes (like Edge/Chrome) to release file handles
    if initial_wait > 0:
        logger.debug(f"Waiting {initial_wait} seconds before attempting to delete: {file_path}")
        time.sleep(initial_wait)
    
    for attempt in range(1, max_retries + 1):
        try:
            os.unlink(file_path)
            logger.info(f"Successfully deleted file on attempt {attempt}: {file_path}")
            return True
        except PermissionError as e:
            if attempt < max_retries:
                logger.warning(
                    f"Failed to delete file (attempt {attempt}/{max_retries}): {file_path}. "
                    f"Error: {e}. Retrying in {retry_delay} seconds..."
                )
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Failed to delete file after {max_retries} attempts: {file_path}. "
                    f"Error: {e}. File may be locked by another process."
                )
        except FileNotFoundError:
            # File was already deleted (possibly by another process)
            logger.debug(f"File already deleted: {file_path}")
            return True
        except Exception as e:
            logger.error(
                f"Unexpected error deleting file (attempt {attempt}/{max_retries}): {file_path}. "
                f"Error: {type(e).__name__}: {e}"
            )
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return False
    
    return False


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
        return {"success": False, "error": f"שגיאה: {str(e)}"}


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
        return {"success": False, "error": f"שגיאה: {str(e)}"}


def generate_guide_pdf(conn, person_id: int, year: int, month: int) -> Optional[bytes]:
    """Generate PDF for guide report using Headless Edge over local file."""
    import subprocess
    import tempfile
    import os
    import re
    import time
    from fastapi.testclient import TestClient
    from core.config import config
    
    # Import app inside function to avoid circular dependency
    try:
        from app import app
    except ImportError:
        logger.error("Could not import app for PDF generation")
        return None

    temp_html_path = None
    temp_pdf_path = None
    process = None

    try:
        # 1. Render HTML using TestClient (internal execution, no network deadlock)
        client = TestClient(app)
        response = client.get(f"/guide/{person_id}?year={year}&month={month}")
        
        if response.status_code != 200:
            logger.error(f"Failed to render guide page: {response.status_code}")
            return None
            
        html_content = response.text
        
        # 2. Fix static assets for file:// access
        # Convert /static/path to file:///absolute/path/static/path
        if config.STATIC_DIR:
            static_base_uri = config.STATIC_DIR.as_uri()
            # Ensure it ends with / if needed, though as_uri usually doesn't for dirs?
            # actually as_uri on Windows path might be file:///C:/.../static
            # We want to replace all "/static/" references.
            
            # Simple replace: href="/static/css..." -> href="file:///.../static/css..."
            # We strip the leading slash from the uri if present in replacement
            # static_base_uri usually looks like 'file:///F:/.../static'
            
            html_content = html_content.replace('"/static/', f'"{static_base_uri}/')
            html_content = html_content.replace("'/static/", f"'{static_base_uri}/")

        # 3. Save to temp HTML file
        fd, temp_html_path = tempfile.mkstemp(suffix='.html')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        # 4. Prepare temp PDF path
        fd_pdf, temp_pdf_path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd_pdf) # Just reserve the name
        
        # 5. Find Browser (Edge or Chrome)
        # We try standard paths for both
        browser_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ]
        
        browser_exe = None
        for path in browser_paths:
            if os.path.exists(path):
                browser_exe = path
                break
        
        if not browser_exe:
            logger.error("No suitable browser (Edge/Chrome) found for PDF generation")
            return None

        cmd = [
            browser_exe,
            "--headless",
            "--disable-gpu",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            "--no-pdf-header-footer",
            f"--print-to-pdf={temp_pdf_path}",
            temp_html_path
        ]

        logger.info(f"Generating PDF using browser from local file: {temp_html_path}")
        logger.info(f"Running browser command: {cmd}")
        
        # Use Popen for better process control
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        # Wait for process to complete with timeout
        try:
            stdout, stderr = process.communicate(timeout=45)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            logger.error("Browser process timed out after 45 seconds")
            process.kill()
            process.wait()
            return None
        finally:
            # Ensure process is terminated
            if process.poll() is None:
                logger.warning("Browser process still running, terminating...")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Browser process did not terminate, killing...")
                    process.kill()
                    process.wait()

        logger.info(f"Browser return code: {return_code}")
        if stdout:
            logger.info(f"Browser stdout: {stdout.decode('utf-8', errors='ignore')}")
        if stderr:
            logger.info(f"Browser stderr: {stderr.decode('utf-8', errors='ignore')}")

        # Wait for browser to fully release file handles (Windows-specific issue)
        logger.debug("Waiting for browser to release file handles...")
        time.sleep(2)

        # Check PDF before cleanup
        pdf_exists = os.path.exists(temp_pdf_path)
        pdf_size = os.path.getsize(temp_pdf_path) if pdf_exists else 0
        logger.info(f"PDF check - exists: {pdf_exists}, size: {pdf_size}, path: {temp_pdf_path}")

        if return_code != 0:
            logger.error(f"Browser PDF generation error: {stderr.decode('utf-8', errors='ignore')}")
            # Continue to check if file exists anyway

        if pdf_exists and pdf_size > 0:
            with open(temp_pdf_path, "rb") as f:
                pdf_bytes = f.read()
            logger.info(f"PDF generated successfully, size: {len(pdf_bytes)} bytes")
            return pdf_bytes
        else:
            logger.error("PDF file was not created or is empty")
            return None

    except Exception as e:
        logger.error(f"Error generating PDF: {e}", exc_info=True)
        return None
    
    finally:
        # Cleanup temp files with retry mechanism
        if temp_html_path:
            logger.debug(f"Cleaning up HTML temp file: {temp_html_path}")
            safe_delete_file(temp_html_path, initial_wait=1.0)
        
        if temp_pdf_path:
            logger.debug(f"Cleaning up PDF temp file: {temp_pdf_path}")
            safe_delete_file(temp_pdf_path, initial_wait=1.0)


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
        return {"success": False, "error": str(e)}


def send_guide_email(conn, person_id: int, year: int, month: int, custom_email: Optional[str] = None) -> Dict[str, Any]:
    """Send guide report email to a specific person or custom email address."""
    try:
        # Get email settings
        settings = get_email_settings(conn)
        if not settings:
            return {"success": False, "error": "הגדרות מייל לא נמצאו. אנא הגדר אותן בעמוד ההגדרות."}

        # Get person info
        person = conn.execute(
            "SELECT id, name, email FROM people WHERE id = %s",
            (person_id,)
        ).fetchone()

        if not person:
            return {"success": False, "error": "מדריך לא נמצא"}

        # Use custom email if provided, otherwise use person's email
        target_email = custom_email if custom_email else person['email']

        if not target_email:
            return {"success": False, "error": f"למדריך {person['name']} אין כתובת מייל"}

        # Generate PDF
        pdf_bytes = generate_guide_pdf(conn, person_id, year, month)
        if not pdf_bytes:
            return {"success": False, "error": "שגיאה ביצירת PDF"}

        # Prepare email content
        subject = f"דוח פירוט שעות עבודה כנספח לתלוש השכר חודש {month:02d}/{year}"
        body = f"""שלום {person['name']},

מצורף דוח פירוט שעות העבודה והתשלום לחודש {month:02d}/{year}.

בברכה,
מדור שכר
צהר הלב
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
        return {"success": False, "error": str(e)}


def send_all_guides_email(conn, year: int, month: int) -> Dict[str, Any]:
    """Send guide report emails to all active guides with email addresses."""
    try:
        # Get email settings
        settings = get_email_settings(conn)
        if not settings:
            return {"success": False, "error": "הגדרות מייל לא נמצאו"}

        # Get all active guides with emails
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
            result = send_guide_email(conn, guide['id'], year, month)
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
        return {"success": False, "error": str(e)}


def send_all_guides_to_single_email(conn, year: int, month: int, target_email: str) -> Dict[str, Any]:
    """שליחת כל דוחות המדריכים למייל אחד (קובץ PDF אחד משולב)."""
    try:
        # Get email settings
        settings = get_email_settings(conn)
        if not settings:
            return {"success": False, "error": "הגדרות מייל לא נמצאו"}

        if not target_email:
            return {"success": False, "error": "לא הוזנה כתובת מייל"}

        # Get all active guides with shifts this month
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

        # Generate combined PDF with all guides
        pdf_bytes, guide_count, failed_guides = _generate_combined_guides_pdf(
            conn, guides, year, month
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

בברכה,
מערכת דיור003
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
        return {"success": False, "error": str(e)}


def _generate_combined_guides_pdf(conn, guides, year: int, month: int):
    """יצירת קובץ PDF אחד משולב עם כל המדריכים - כל מדריך בעמוד נפרד.

    שולף נתונים ישירות מ-time_reports כמו בדוח הבודד.
    """
    import calendar
    from datetime import datetime, date
    from typing import Dict
    from jinja2 import Environment, FileSystemLoader
    from core.config import config
    from core.database import get_conn
    from core.time_utils import span_minutes, get_shabbat_times_cache
    from utils.utils import month_range_ts
    from core.history import get_minimum_wage_for_month
    from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly

    failed_guides = []
    successful_guides = 0

    # Hebrew day names
    def _get_hebrew_day_name(d) -> str:
        """מחזיר שם יום בעברית."""
        day_map = {0: 'ב', 1: 'ג', 2: 'ד', 3: 'ה', 4: 'ו', 5: 'ש', 6: 'א'}
        return day_map.get(d.weekday(), '')

    def _calculate_segment_hours(start_time_str, end_time_str, shift_type_id, segments_by_shift):
        """חישוב שעות עבודה וכוננות לפי סגמנטים."""
        if not start_time_str or not end_time_str:
            return 0.0, 0.0

        actual_start, actual_end = span_minutes(start_time_str, end_time_str)
        total_work = 0.0
        total_standby = 0.0

        segment_list = segments_by_shift.get(shift_type_id, [])
        if not segment_list:
            # אין סגמנטים - הכל עבודה
            total_minutes = actual_end - actual_start
            if total_minutes < 0:
                total_minutes += 24 * 60
            return round(total_minutes / 60, 2), 0.0

        for seg in segment_list:
            seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])
            overlap_start = max(seg_start, actual_start)
            overlap_end = min(seg_end, actual_end)

            if overlap_end > overlap_start:
                overlap_minutes = overlap_end - overlap_start
                if seg.get("segment_type") == "standby":
                    total_standby += overlap_minutes
                else:
                    total_work += overlap_minutes

        return round(total_work / 60, 2), round(total_standby / 60, 2)

    try:
        # Setup Jinja2 template
        env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
        template = env.get_template("guide_shifts_pdf.html")

        # Collect HTML content from all guides
        html_parts = []

        # תאריכי החודש
        start_dt, end_dt = month_range_ts(year, month)
        start_date = start_dt.date()
        end_date = end_dt.date()

        for i, guide in enumerate(guides):
            try:
                person_id = guide['id']
                person_name = guide['name']

                # Get person info
                person = conn.execute(
                    "SELECT id, name, email, type FROM people WHERE id = %s",
                    (person_id,)
                ).fetchone()

                if not person:
                    failed_guides.append(person_name)
                    continue

                # שליפת משמרות מ-time_reports
                reports = conn.execute("""
                    SELECT
                        tr.id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                        st.name AS shift_type_name,
                        a.name AS apartment_name
                    FROM time_reports tr
                    LEFT JOIN shift_types st ON tr.shift_type_id = st.id
                    LEFT JOIN apartments a ON tr.apartment_id = a.id
                    WHERE tr.person_id = %s
                      AND tr.date >= %s AND tr.date < %s
                    ORDER BY tr.date, tr.start_time
                """, (person_id, start_date, end_date)).fetchall()

                # שליפת סגמנטים
                shift_ids = list({r["shift_type_id"] for r in reports if r["shift_type_id"]})
                segments_by_shift = {}
                if shift_ids:
                    placeholders = ",".join(["%s"] * len(shift_ids))
                    segments = conn.execute(f"""
                        SELECT shift_type_id, segment_type, start_time, end_time
                        FROM shift_time_segments
                        WHERE shift_type_id IN ({placeholders})
                        ORDER BY shift_type_id, order_index
                    """, tuple(shift_ids)).fetchall()
                    for seg in segments:
                        segments_by_shift.setdefault(seg["shift_type_id"], []).append(seg)

                # בניית שורות הדוח
                shifts_data = []
                total_work_hours = 0.0
                standby_count = 0

                for r in reports:
                    r_date = r["date"]
                    if isinstance(r_date, datetime):
                        r_date = r_date.date()

                    # עיבוד שם סוג משמרת
                    shift_name = r["shift_type_name"] or ""
                    if shift_name.startswith("משמרת "):
                        shift_name = shift_name[len("משמרת "):]

                    # בדיקה אם תגבור
                    is_tagbor = "תגבור" in shift_name
                    segment_list = segments_by_shift.get(r["shift_type_id"], [])

                    if is_tagbor and segment_list and r["start_time"] and r["end_time"]:
                        # תצוגה מיוחדת למשמרת תגבור
                        actual_start, actual_end = span_minutes(r["start_time"], r["end_time"])
                        overlapping_segments = []
                        for seg in segment_list:
                            seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])
                            overlap_start = max(seg_start, actual_start)
                            overlap_end = min(seg_end, actual_end)
                            if overlap_end > overlap_start:
                                overlapping_segments.append({
                                    "overlap_start": overlap_start,
                                    "overlap_end": overlap_end,
                                    "segment_type": seg.get("segment_type", "work"),
                                })

                        first_segment = True
                        for idx, seg_data in enumerate(overlapping_segments):
                            is_last_segment = (idx == len(overlapping_segments) - 1)
                            overlap_start = seg_data["overlap_start"]
                            overlap_end = seg_data["overlap_end"]
                            segment_type = seg_data["segment_type"]

                            is_friday_tagbor = (r["shift_type_id"] == 108)
                            is_shabbat_tagbor = (r["shift_type_id"] == 109)

                            if first_segment and is_friday_tagbor:
                                display_start = f"{(actual_start // 60) % 24:02d}:{actual_start % 60:02d}"
                                calc_start = actual_start
                            else:
                                display_start = f"{(overlap_start // 60) % 24:02d}:{overlap_start % 60:02d}"
                                calc_start = overlap_start

                            if is_last_segment and is_shabbat_tagbor:
                                display_end = f"{(actual_end // 60) % 24:02d}:{actual_end % 60:02d}"
                                calc_end = actual_end
                            else:
                                display_end = f"{(overlap_end // 60) % 24:02d}:{overlap_end % 60:02d}"
                                calc_end = overlap_end

                            segment_minutes = calc_end - calc_start
                            segment_hours = round(segment_minutes / 60, 2)

                            if segment_type == "standby":
                                work_hours = 0.0
                                standby_hours = segment_hours
                            else:
                                work_hours = segment_hours
                                standby_hours = 0.0

                            total_work_hours += work_hours
                            if standby_hours > 0:
                                standby_count += 1

                            shifts_data.append({
                                "date": r_date.strftime("%d/%m/%y") if first_segment else "",
                                "day": _get_hebrew_day_name(r_date) if first_segment else "",
                                "apartment": r["apartment_name"] or "" if first_segment else "",
                                "shift_type": shift_name if first_segment else "",
                                "start_time": display_start,
                                "end_time": display_end,
                                "work_hours": work_hours,
                                "standby_hours": standby_hours,
                                "tagbor_group": True,
                                "tagbor_first": first_segment,
                                "tagbor_last": is_last_segment,
                            })
                            first_segment = False

                    elif r["shift_type_id"] == 107 and r["start_time"] and r["end_time"]:
                        # משמרת לילה - חישוב מיוחד
                        actual_start, actual_end = span_minutes(r["start_time"], r["end_time"])
                        FIRST_WORK_MINUTES = 120
                        STANDBY_END_MINUTES = 6 * 60 + 30

                        work_end_first = actual_start + FIRST_WORK_MINUTES
                        first_work_minutes = min(FIRST_WORK_MINUTES, actual_end - actual_start)

                        standby_start = work_end_first
                        if actual_end < actual_start:
                            actual_end_adjusted = actual_end + 24 * 60
                        else:
                            actual_end_adjusted = actual_end

                        if actual_start >= 12 * 60:
                            standby_end_target = STANDBY_END_MINUTES + 24 * 60
                        else:
                            standby_end_target = STANDBY_END_MINUTES

                        standby_end = min(standby_end_target, actual_end_adjusted)
                        standby_minutes = max(0, standby_end - standby_start)

                        morning_work_start = standby_end_target
                        morning_work_minutes = max(0, actual_end_adjusted - morning_work_start)

                        work_hours = round((first_work_minutes + morning_work_minutes) / 60, 2)
                        standby_hours = round(standby_minutes / 60, 2)

                        total_work_hours += work_hours
                        if standby_hours > 0:
                            standby_count += 1

                        shifts_data.append({
                            "date": r_date.strftime("%d/%m/%y"),
                            "day": _get_hebrew_day_name(r_date),
                            "apartment": r["apartment_name"] or "",
                            "shift_type": shift_name,
                            "start_time": r["start_time"][:5] if r["start_time"] else "",
                            "end_time": r["end_time"][:5] if r["end_time"] else "",
                            "work_hours": round(work_hours, 2),
                            "standby_hours": round(standby_hours, 2),
                        })
                    else:
                        # תצוגה רגילה
                        work_hours, standby_hours = 0.0, 0.0
                        if r["start_time"] and r["end_time"]:
                            work_hours, standby_hours = _calculate_segment_hours(
                                r["start_time"], r["end_time"],
                                r["shift_type_id"], segments_by_shift
                            )

                        total_work_hours += work_hours
                        if standby_hours > 0:
                            standby_count += 1

                        shifts_data.append({
                            "date": r_date.strftime("%d/%m/%y"),
                            "day": _get_hebrew_day_name(r_date),
                            "apartment": r["apartment_name"] or "",
                            "shift_type": shift_name,
                            "start_time": r["start_time"][:5] if r["start_time"] else "",
                            "end_time": r["end_time"][:5] if r["end_time"] else "",
                            "work_hours": round(work_hours, 2),
                            "standby_hours": round(standby_hours, 2),
                        })

                # שליפת תשלומים נוספים
                payment_comps = conn.execute("""
                    SELECT
                        pc.quantity, pc.rate, pc.description,
                        pct.name AS component_type_name
                    FROM payment_components pc
                    LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
                    WHERE pc.person_id = %s
                      AND pc.date >= %s AND pc.date < %s
                    ORDER BY pc.date
                """, (person_id, start_date, end_date)).fetchall()

                payments_by_type: Dict[str, float] = {}
                total_additions = 0.0
                for pc in payment_comps:
                    # תעריפים באגורות - מחלקים ב-100
                    amount = (pc["quantity"] * pc["rate"]) / 100
                    total_additions += amount
                    # סיכום לפי סוג תשלום
                    type_name = pc["component_type_name"] or "אחר"
                    if pc["description"]:
                        key = f"{type_name} - {pc['description']}"
                    else:
                        key = type_name
                    payments_by_type[key] = payments_by_type.get(key, 0) + amount

                payments_data = [
                    {"description": desc, "amount": round(amt, 2)}
                    for desc, amt in payments_by_type.items()
                ]

                # חישוב תעריפים משתנים מ-daily_segments
                MINIMUM_WAGE = get_minimum_wage_for_month(conn.conn, year, month)
                shabbat_cache = get_shabbat_times_cache(conn.conn)

                with get_conn() as temp_conn:
                    daily_segments, _ = get_daily_segments_data(
                        temp_conn, person_id, year, month, shabbat_cache, MINIMUM_WAGE
                    )
                    monthly_totals = aggregate_daily_segments_to_monthly(
                        temp_conn, daily_segments, person_id, year, month, MINIMUM_WAGE
                    )

                variable_by_shift = {}
                for day in daily_segments:
                    for chain in day.get("chains", []):
                        chain_shift_name = chain.get("shift_name", "") or ""
                        chain_rate = chain.get("effective_rate", MINIMUM_WAGE) or MINIMUM_WAGE

                        if not chain_shift_name:
                            continue

                        is_special_hourly = chain.get("is_special_hourly", False)
                        is_variable_rate = is_special_hourly or abs(chain_rate - MINIMUM_WAGE) > 0.01

                        if is_variable_rate:
                            calc100 = chain.get("calc100", 0) or 0
                            calc125 = chain.get("calc125", 0) or 0
                            calc150 = chain.get("calc150", 0) or 0
                            calc150_shabbat = chain.get("calc150_shabbat", 0) or 0
                            calc175 = chain.get("calc175", 0) or 0
                            calc200 = chain.get("calc200", 0) or 0
                            total_minutes = calc100 + calc125 + calc150 + calc175 + calc200
                            shabbat_minutes = calc150_shabbat + calc175 + calc200

                            if total_minutes <= 0:
                                continue

                            rounded_rate = round(chain_rate, 2)
                            h100 = round(calc100 / 60, 2)
                            h125 = round(calc125 / 60, 2)
                            h150 = round(calc150 / 60, 2)
                            h175 = round(calc175 / 60, 2)
                            h200 = round(calc200 / 60, 2)

                            gesher_payment = (
                                h100 * 1.0 * rounded_rate +
                                h125 * 1.25 * rounded_rate +
                                h150 * 1.5 * rounded_rate +
                                h175 * 1.75 * rounded_rate +
                                h200 * 2.0 * rounded_rate +
                                (chain.get("escort_bonus_pay", 0) or 0)
                            )

                            group_key = (chain_shift_name, rounded_rate)
                            if group_key not in variable_by_shift:
                                variable_by_shift[group_key] = {
                                    "shift_name": chain_shift_name,
                                    "minutes": 0,
                                    "shabbat_minutes": 0,
                                    "payment": 0,
                                    "rate": rounded_rate
                                }
                            variable_by_shift[group_key]["minutes"] += total_minutes
                            variable_by_shift[group_key]["shabbat_minutes"] += shabbat_minutes
                            variable_by_shift[group_key]["payment"] += gesher_payment

                # בדיקה אילו משמרות יש להן תעריפים שונים
                shift_names_with_multiple_rates = set()
                shift_name_rates = {}
                for (shift_name, rate), data in variable_by_shift.items():
                    if shift_name not in shift_name_rates:
                        shift_name_rates[shift_name] = set()
                    shift_name_rates[shift_name].add(rate)
                for shift_name, rates in shift_name_rates.items():
                    if len(rates) > 1:
                        shift_names_with_multiple_rates.add(shift_name)

                variable_shifts = []
                for group_key, data in variable_by_shift.items():
                    hours = round(data["minutes"] / 60, 2)
                    payment = round(data["payment"], 1)
                    rate = data["rate"]
                    base_shift_name = data["shift_name"]

                    if base_shift_name in shift_names_with_multiple_rates:
                        is_shabbat = data["shabbat_minutes"] > (data["minutes"] * 0.5)
                        display_name = f"{base_shift_name} (שבת)" if is_shabbat else f"{base_shift_name} (חול)"
                    else:
                        display_name = base_shift_name

                    base_payment = round(hours * rate, 2)
                    overtime_payment = round(payment - base_payment, 1)
                    variable_shifts.append({
                        "shift_name": display_name,
                        "hours": hours,
                        "rate": rate,
                        "overtime_payment": overtime_payment,
                        "payment": payment
                    })

                # Calculate period dates
                last_day = calendar.monthrange(year, month)[1]
                period_start = f"01/{month:02d}/{str(year)[2:]}"
                period_end = f"{last_day}/{month:02d}/{str(year)[2:]}"
                generation_time = datetime.now(config.LOCAL_TZ).strftime("%H:%M:%S %d.%m.%Y")

                summary_total_salary = monthly_totals.get("rounded_total", 0)
                variable_rate_total = round(monthly_totals.get("payment_calc_variable", 0) or 0, 1)

                # Render template for this guide
                guide_html = template.render(
                    person=dict(person),
                    shifts_data=shifts_data,
                    payments_data=payments_data,
                    total_work_hours=round(total_work_hours, 2),
                    standby_count=standby_count,
                    total_additions=round(total_additions, 2),
                    total_salary=round(summary_total_salary, 2),
                    period_start=period_start,
                    period_end=period_end,
                    generation_time=generation_time,
                    variable_shifts=variable_shifts,
                    variable_rate_total=variable_rate_total,
                )

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

        # יצירת PDF באמצעות דפדפן headless
        import subprocess
        import tempfile
        import time

        temp_html_path = None
        temp_pdf_path = None

        try:
            # Save to temp HTML file
            fd, temp_html_path = tempfile.mkstemp(suffix='.html')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(combined_html)

            # Prepare temp PDF path
            fd_pdf, temp_pdf_path = tempfile.mkstemp(suffix='.pdf')
            os.close(fd_pdf)

            # Find Browser
            browser_paths = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            ]

            browser_exe = None
            for path in browser_paths:
                if os.path.exists(path):
                    browser_exe = path
                    break

            if not browser_exe:
                logger.error("No suitable browser (Edge/Chrome) found for PDF generation")
                return None, 0, failed_guides

            cmd = [
                browser_exe,
                "--headless",
                "--disable-gpu",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=10000",
                "--no-pdf-header-footer",
                f"--print-to-pdf={temp_pdf_path}",
                temp_html_path
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            try:
                stdout, stderr = process.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                logger.error("Browser process timed out")
                process.kill()
                process.wait()
                return None, 0, failed_guides
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

            time.sleep(1)

            if os.path.exists(temp_pdf_path) and os.path.getsize(temp_pdf_path) > 0:
                with open(temp_pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                logger.info(f"Combined PDF generated successfully, size: {len(pdf_bytes)} bytes")
                return pdf_bytes, successful_guides, failed_guides
            else:
                logger.error("Combined PDF file was not created or is empty")
                return None, 0, failed_guides

        finally:
            if temp_html_path:
                safe_delete_file(temp_html_path, initial_wait=1.0)
            if temp_pdf_path:
                safe_delete_file(temp_pdf_path, initial_wait=1.0)

    except Exception as e:
        logger.error(f"Error generating combined PDF: {e}", exc_info=True)
        return None, 0, failed_guides


