# -*- coding: utf-8 -*-
"""בדיקות להשלמות תומך מקצועי המשולמות ידנית ואינן יוצאות לגשר."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS, MANUAL_COMPLETION_SYMBOLS
from services import gesher_difference


def _professional_support_diff(amount: float = 150.0) -> dict:
    return {
        "internal_key": "professional_support",
        "symbol": "243",
        "employee_code": "1234",
        "employer_code": "001",
        "person_id": 1,
        "person_name": "אבי",
        "amount_diff": amount,
        "quantity_diff": 0.0,
        "rate": 0.0,
    }


class ManualCompletionTargetTests(unittest.TestCase):
    def test_professional_support_maps_to_manual_symbol(self):
        target = gesher_difference._completion_target_for_diff(_professional_support_diff())
        self.assertEqual(target, "243")

    def test_shared_constants_describe_the_manual_component(self):
        self.assertIn("243", MANUAL_COMPLETION_SYMBOLS)
        self.assertIn(13, MANUAL_COMPLETION_COMPONENT_TYPE_IDS)

    def test_mapping_is_by_internal_key_not_by_source_symbol(self):
        """עריכת סמל המקור במסך סמלי שכר לא אמורה לשבור את המיפוי."""
        diff = {**_professional_support_diff(), "symbol": "999"}
        self.assertEqual(gesher_difference._completion_target_for_diff(diff), "243")

    def test_professional_support_produces_a_completion_row(self):
        rows = gesher_difference.build_completion_gesher_rows([_professional_support_diff()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "243")
        self.assertEqual(rows[0]["amount"], 150.0)

    def test_row_gets_the_manual_display_name(self):
        rows = gesher_difference.finalize_completion_rows(
            gesher_difference.build_completion_gesher_rows([_professional_support_diff()])
        )
        self.assertEqual(rows[0]["display_name"], "תומך מקצועי - לתשלום ידני")


if __name__ == "__main__":
    unittest.main()
