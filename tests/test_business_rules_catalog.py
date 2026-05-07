# -*- coding: utf-8 -*-
"""
בדיקות לקטלוג הכללים העסקיים.

הקטלוג אינו מקור החישוב עצמו, אבל הוא מוצג למשתמשת כמפת כללים חיה.
לכן כדאי להגן עליו מפני טבלאות שבורות או תיאור שסותר התנהגות בסיסית בקוד.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.business_rules_catalog import (
    get_business_rule_reference_tables,
    get_business_rule_sections,
)
from services.gesher_exporter import calculate_value


class TestBusinessRulesCatalog(unittest.TestCase):
    """בדיקות תקינות מבנה ותוכן בסיסי לקטלוג הכללים."""

    def test_sections_have_unique_keys_and_rules(self):
        sections = get_business_rule_sections()

        self.assertGreaterEqual(len(sections), 1)
        keys = [section.key for section in sections]
        self.assertEqual(len(keys), len(set(keys)))

        for section in sections:
            self.assertTrue(section.title)
            self.assertTrue(section.description)
            self.assertGreaterEqual(len(section.rules), 1)
            for rule in section.rules:
                self.assertTrue(rule.title)
                self.assertTrue(rule.summary)
                self.assertTrue(rule.source, f"Missing source for rule: {rule.title}")

    def test_reference_tables_have_consistent_shape(self):
        tables = get_business_rule_reference_tables()

        self.assertGreaterEqual(len(tables), 1)
        keys = [table.key for table in tables]
        self.assertEqual(len(keys), len(set(keys)))

        for table in tables:
            self.assertTrue(table.title)
            self.assertTrue(table.description)
            self.assertGreaterEqual(len(table.columns), 2)
            self.assertGreaterEqual(len(table.rows), 1)
            self.assertTrue(table.source, f"Missing source for table: {table.title}")
            for row in table.rows:
                self.assertEqual(
                    len(row),
                    len(table.columns),
                    f"Row length mismatch in table {table.key}: {row}",
                )

    def test_required_reference_tables_exist(self):
        table_keys = {table.key for table in get_business_rule_reference_tables()}

        self.assertIn("shift-types", table_keys)
        self.assertIn("employee-types", table_keys)
        self.assertIn("apartment-types", table_keys)
        self.assertIn("housing-arrays", table_keys)
        self.assertIn("export-components", table_keys)

    def test_housing_array_table_documents_asd_and_regular_array(self):
        housing_table = next(
            table for table in get_business_rule_reference_tables()
            if table.key == "housing-arrays"
        )
        table_text = "\n".join(" | ".join(row) for row in housing_table.rows)

        self.assertIn("צוהר הלב", table_text)
        self.assertIn("ASD", table_text)
        self.assertIn("housing_array_id=2", table_text)
        self.assertIn("חג מלא", table_text)

    def test_gesher_standby_description_matches_calculate_value(self):
        quantity, rate = calculate_value(
            {"standby_payment": 210.0},
            "standby",
            "standby_with_rate",
            34.40,
        )
        self.assertEqual((quantity, rate), (1.0, 210.0))

        export_table = next(
            table for table in get_business_rule_reference_tables()
            if table.key == "export-components"
        )
        standby_row = next(row for row in export_table.rows if row[0] == "standby")

        self.assertIn("כמות 1", standby_row[2])
        self.assertIn("סכום הכוננות הכולל", standby_row[2])


if __name__ == "__main__":
    unittest.main()
