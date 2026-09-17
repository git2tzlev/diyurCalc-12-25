"""Shared HTML-to-PDF rendering helpers."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)


BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def safe_delete_file(
    file_path: str,
    max_retries: int = 5,
    retry_delay: float = 1.0,
    initial_wait: float = 2.0,
) -> bool:
    """Delete a temporary file with retries for Windows file locking."""
    if not file_path or not os.path.exists(file_path):
        return True

    if initial_wait > 0:
        time.sleep(initial_wait)

    for attempt in range(max_retries):
        try:
            os.unlink(file_path)
            return True
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logger.warning("Could not delete locked temp file: %s", file_path)
                return False
        except FileNotFoundError:
            return True
        except Exception as e:
            logger.warning("Could not delete temp file %s: %s", file_path, e)
            return False
    return False


def render_html_to_pdf_bytes(
    html_content: str,
    *,
    timeout_seconds: int = 45,
    settle_seconds: float = 2.0,
) -> Optional[bytes]:
    """Render HTML content to PDF bytes using installed Edge/Chrome."""
    temp_html_path = None
    temp_pdf_path = None
    browser_profile_dir = None
    try:
        fd, temp_html_path = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_content)

        fd_pdf, temp_pdf_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd_pdf)
        browser_profile_dir = tempfile.mkdtemp(prefix="diyur-pdf-profile-")

        browser_exe = next((path for path in BROWSER_PATHS if os.path.exists(path)), None)
        if not browser_exe:
            logger.error("No suitable browser found for PDF generation")
            return None

        cmd = [
            browser_exe,
            "--headless",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            "--no-pdf-header-footer",
            f"--user-data-dir={browser_profile_dir}",
            f"--print-to-pdf={temp_pdf_path}",
            temp_html_path,
        ]
        try:
            process = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("Browser process timed out")
            return None

        if process.returncode != 0:
            stderr = process.stderr.decode("utf-8", errors="replace").strip()
            logger.error(
                "Browser PDF process failed with exit code %s: %s",
                process.returncode,
                stderr[-2000:] if stderr else "no stderr",
            )
            return None

        time.sleep(settle_seconds)

        if os.path.exists(temp_pdf_path) and os.path.getsize(temp_pdf_path) > 0:
            with open(temp_pdf_path, "rb") as f:
                return f.read()
        logger.error("PDF file was not created or is empty")
        return None
    finally:
        if temp_html_path:
            safe_delete_file(temp_html_path, initial_wait=1.0)
        if temp_pdf_path:
            safe_delete_file(temp_pdf_path, initial_wait=1.0)
        if browser_profile_dir:
            shutil.rmtree(browser_profile_dir, ignore_errors=True)
