"""
Unit tests for the inventory quality and recall tool.
"""

import unittest

from coded_tools.inventory_ops.inventory_quality_tool import InventoryQualityTool


class TestInventoryQualityTool(unittest.TestCase):
    """Verify QUA question coverage and sellability guardrails."""

    def setUp(self) -> None:
        self.tool = InventoryQualityTool()

    def test_unsellable_summary_separates_usable_and_unusable(self) -> None:
        result = self.tool.invoke({"action": "unsellable_summary"}, {})
        self.assertGreaterEqual(result["summary"]["unusable_units"], 5)
        self.assertGreater(result["summary"]["unusable_value_inr"], 0)
        states = {item["state"] for item in result["unusable_batches"]}
        self.assertIn("recalled", states)

    def test_expiry_risk_flags_expired_batch(self) -> None:
        result = self.tool.invoke({"action": "expiry_risk", "horizon_days": 45}, {})
        self.assertGreaterEqual(result["summary"]["expired_batches"], 1)
        self.assertTrue(any(item["priority"] == "EXPIRED" for item in result["items"]))

    def test_return_disposition_routes_damaged_to_quarantine(self) -> None:
        damaged = self.tool.invoke({"action": "return_disposition", "return_id": "RET-003"}, {})
        self.assertEqual(damaged["items"][0]["disposition"], "quarantine")
        resellable = self.tool.invoke({"action": "return_disposition", "return_id": "RET-001"}, {})
        self.assertEqual(resellable["items"][0]["disposition"], "restock")

    def test_recurring_discrepancy_finds_sku_001(self) -> None:
        result = self.tool.invoke({"action": "recurring_discrepancy"}, {})
        patterns = {item["sku"] for item in result["patterns"]}
        self.assertIn("SKU-001", patterns)

    def test_quality_signals_escalate_sku_003(self) -> None:
        result = self.tool.invoke({"action": "quality_signals"}, {})
        sku_003 = next(item for item in result["items"] if item["sku"] == "SKU-003")
        self.assertTrue(sku_003["suspected_quality_issue"])

    def test_recall_check_detects_channel_exposure(self) -> None:
        result = self.tool.invoke({"action": "recall_check", "sku": "SKU-003"}, {})
        self.assertTrue(result["summary"]["selling_recalled_stock"])
        self.assertGreater(result["summary"]["channel_exposure_units"], 0)

    def test_explain_variance_reports_unexplained_difference(self) -> None:
        result = self.tool.invoke({"action": "explain_variance", "sku": "SKU-006"}, {})
        self.assertNotEqual(result["summary"]["unexplained_difference_units"], 0)

    def test_cycle_count_priority_returns_ranked_items(self) -> None:
        result = self.tool.invoke({"action": "cycle_count_priority", "limit": 5}, {})
        self.assertEqual(len(result["items"]), 5)
        self.assertGreaterEqual(result["items"][0]["priority_score"], result["items"][-1]["priority_score"])


if __name__ == "__main__":
    unittest.main()
