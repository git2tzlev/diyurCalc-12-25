# -*- coding: utf-8 -*-
"""Regression tests for isolated browser PDF rendering."""
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import pdf_renderer


class _CompletedProcess:
    returncode = 0
    stdout = b""
    stderr = b""


class TestPdfRenderer(unittest.TestCase):
    def test_render_uses_an_isolated_temporary_browser_profile(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            pdf_arg = next(arg for arg in cmd if arg.startswith("--print-to-pdf="))
            with open(pdf_arg.split("=", 1)[1], "wb") as pdf_file:
                pdf_file.write(b"%PDF-test")
            return _CompletedProcess()

        with (
            patch.object(pdf_renderer, "BROWSER_PATHS", [sys.executable]),
            patch.object(pdf_renderer.subprocess, "run", side_effect=fake_run),
        ):
            result = pdf_renderer.render_html_to_pdf_bytes(
                "<html></html>", settle_seconds=0
            )

        profile_args = [
            arg for arg in captured["cmd"] if arg.startswith("--user-data-dir=")
        ]
        self.assertEqual(result, b"%PDF-test")
        self.assertEqual(len(profile_args), 1)
        self.assertFalse(os.path.exists(profile_args[0].split("=", 1)[1]))

    def test_render_rejects_failed_browser_exit_even_if_output_exists(self):
        class _FailedProcess(_CompletedProcess):
            returncode = 1
            stderr = b"browser failed"

        def fake_run(cmd, **kwargs):
            pdf_arg = next(arg for arg in cmd if arg.startswith("--print-to-pdf="))
            with open(pdf_arg.split("=", 1)[1], "wb") as pdf_file:
                pdf_file.write(b"partial")
            return _FailedProcess()

        with (
            patch.object(pdf_renderer, "BROWSER_PATHS", [sys.executable]),
            patch.object(pdf_renderer.subprocess, "run", side_effect=fake_run),
        ):
            result = pdf_renderer.render_html_to_pdf_bytes(
                "<html></html>", settle_seconds=0
            )

        self.assertIsNone(result)

    def test_render_retries_once_when_browser_creates_an_empty_pdf(self):
        attempts = 0

        def fake_run(cmd, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                pdf_arg = next(arg for arg in cmd if arg.startswith("--print-to-pdf="))
                with open(pdf_arg.split("=", 1)[1], "wb") as pdf_file:
                    pdf_file.write(b"%PDF-retry")
            return _CompletedProcess()

        with (
            patch.object(pdf_renderer, "BROWSER_PATHS", [sys.executable]),
            patch.object(pdf_renderer.subprocess, "run", side_effect=fake_run),
            patch.object(pdf_renderer.time, "sleep", return_value=None),
        ):
            result = pdf_renderer.render_html_to_pdf_bytes(
                "<html></html>", settle_seconds=0
            )

        self.assertEqual(result, b"%PDF-retry")
        self.assertEqual(attempts, 2)

    def test_render_serializes_parallel_browser_processes(self):
        state_lock = threading.Lock()
        active_processes = 0
        max_active_processes = 0

        def fake_run(cmd, **kwargs):
            nonlocal active_processes, max_active_processes
            with state_lock:
                active_processes += 1
                max_active_processes = max(max_active_processes, active_processes)
            threading.Event().wait(0.05)
            pdf_arg = next(arg for arg in cmd if arg.startswith("--print-to-pdf="))
            with open(pdf_arg.split("=", 1)[1], "wb") as pdf_file:
                pdf_file.write(b"%PDF-parallel")
            with state_lock:
                active_processes -= 1
            return _CompletedProcess()

        with (
            patch.object(pdf_renderer, "BROWSER_PATHS", [sys.executable]),
            patch.object(pdf_renderer.subprocess, "run", side_effect=fake_run),
            patch.object(pdf_renderer.time, "sleep", return_value=None),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _: pdf_renderer.render_html_to_pdf_bytes(
                        "<html></html>", settle_seconds=0
                    ),
                    range(2),
                ))

        self.assertEqual(results, [b"%PDF-parallel", b"%PDF-parallel"])
        self.assertEqual(max_active_processes, 1)

    def test_render_does_not_start_next_browser_while_previous_pdf_settles(self):
        state_lock = threading.Lock()
        settling = 0
        browser_started_during_settle = False

        def fake_run(cmd, **kwargs):
            nonlocal browser_started_during_settle
            with state_lock:
                if settling:
                    browser_started_during_settle = True
            pdf_arg = next(arg for arg in cmd if arg.startswith("--print-to-pdf="))
            with open(pdf_arg.split("=", 1)[1], "wb") as pdf_file:
                pdf_file.write(b"%PDF-settled")
            return _CompletedProcess()

        def fake_settle(_seconds):
            nonlocal settling
            with state_lock:
                settling += 1
            threading.Event().wait(0.05)
            with state_lock:
                settling -= 1

        with (
            patch.object(pdf_renderer, "BROWSER_PATHS", [sys.executable]),
            patch.object(pdf_renderer.subprocess, "run", side_effect=fake_run),
            patch.object(pdf_renderer.time, "sleep", side_effect=fake_settle),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _: pdf_renderer.render_html_to_pdf_bytes(
                        "<html></html>", settle_seconds=0.05
                    ),
                    range(2),
                ))

        self.assertEqual(results, [b"%PDF-settled", b"%PDF-settled"])
        self.assertFalse(browser_started_during_settle)


if __name__ == "__main__":
    unittest.main()
