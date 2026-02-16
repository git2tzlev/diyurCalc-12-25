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
        return {"success": False, "error": str(e)}


def send_selected_guides_email(conn, guide_ids: list, year: int, month: int) -> Dict[str, Any]:
    """שליחת דוחות למדריכים נבחרים בלבד (לפי רשימת מזהים)."""
    try:
        logger.info(f"=== התחלת שליחת מיילים ל-{len(guide_ids)} מדריכים - {month:02d}/{year} ===")

        settings = get_email_settings(conn)
        if not settings:
            logger.error("הגדרות מייל לא נמצאו")
            return {"success": False, "error": "הגדרות מייל לא נמצאו"}

        if not guide_ids:
            logger.warning("לא נבחרו מדריכים")
            return {"success": False, "error": "לא נבחרו מדריכים"}

        # שליפת פרטי המדריכים הנבחרים שיש להם מייל
        placeholders = ",".join(["%s"] * len(guide_ids))
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

            result = send_guide_email(conn, guide['id'], year, month)

            if result.get('success'):
                logger.info(f"[{idx}/{total}] ✓ נשלח בהצלחה: {guide_name} -> {guide_email}")
                results['success'].append({"name": guide_name, "email": guide_email})
            else:
                error_msg = result.get('error', 'שגיאה לא ידועה')
                logger.error(f"[{idx}/{total}] ✗ נכשל: {guide_name} ({guide_email}) - {error_msg}")
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
        return {"success": False, "error": str(e)}


def send_selected_guides_to_single_email(conn, guide_ids: list, year: int, month: int, target_email: str) -> Dict[str, Any]:
    """שליחת דוחות מדריכים נבחרים למייל אחד (קובץ PDF משולב)."""
    try:
        settings = get_email_settings(conn)
        if not settings:
            return {"success": False, "error": "הגדרות מייל לא נמצאו"}

        if not target_email:
            return {"success": False, "error": "לא הוזנה כתובת מייל"}

        if not guide_ids:
            return {"success": False, "error": "לא נבחרו מדריכים"}

        # שליפת פרטי המדריכים הנבחרים
        placeholders = ",".join(["%s"] * len(guide_ids))
        guides = conn.execute(f"""
            SELECT id, name
            FROM people
            WHERE id IN ({placeholders})
            ORDER BY name
        """, tuple(guide_ids)).fetchall()

        if not guides:
            return {"success": False, "error": "לא נמצאו מדריכים ברשימה"}

        # Generate combined PDF with selected guides
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
        return {"success": False, "error": str(e)}


def _generate_combined_guides_pdf(conn, guides, year: int, month: int):
    """יצירת קובץ PDF אחד משולב עם כל המדריכים - כל מדריך בעמוד נפרד.

    משתמש ב-prepare_guide_pdf_data מ-routes.guide לקבלת נתונים זהים לדוח הבודד.
    """
    import time
    import subprocess
    import tempfile
    from jinja2 import Environment, FileSystemLoader
    from routes.guide import prepare_guide_pdf_data

    failed_guides = []
    successful_guides = 0

    try:
        # Setup Jinja2 template
        env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
        template = env.get_template("guide_shifts_pdf.html")

        # Collect HTML content from all guides
        html_parts = []

        for guide in guides:
            try:
                person_id = guide['id']
                person_name = guide['name']

                # שימוש בפונקציה המשותפת להכנת נתונים - זהה לדוח הבודד
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

        # יצירת PDF באמצעות דפדפן headless
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
