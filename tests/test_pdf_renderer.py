# -*- coding: utf-8 -*-
"""Regression tests for isolated browser PDF rendering."""
import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
