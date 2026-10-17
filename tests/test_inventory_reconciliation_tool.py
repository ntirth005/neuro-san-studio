"""Unit tests for inventory reconciliation and source-trust behavior."""

import unittest

from coded_tools.inventory_ops.inventory_reconciliation_tool import InventoryReconciliationTool


class TestInventoryReconciliationTool(unittest.TestCase):
    """Cover the inventory visibility and integrity question set."""

    def setUp(self):
        self.tool = InventoryReconciliationTool()

    def test_available_to_sell_keeps_locations_and_incoming_separate(self):
        """INV-01: sellable units aggregate across locations without counting incoming."""
        result = self.tool.invoke({"action": "available_to_sell", "sku": "SKU-001"}, {})
        item = result["items"][0]
        self.assertEqual(item["available_to_sell"], 14)
        self.assertEqual(item["incoming_not_sellable"], 0)
        self.assertEqual(len(item["locations"]), 2)

    def test_source_discrepancies_flag_overstatement_and_overlap(self):
        """INV-02/03/07: channel mismatches and overlapping locations are exposed."""
        result = self.tool.invoke({"action": "source_discrepancies"}, {})
        skus = [item["sku"] for item in result["discrepancies"]]
        self.assertIn("SKU-001", skus)
        self.assertGreater(result["summary"]["overlaps_found"], 0)
        self.assertGreater(result["summary"]["total_financial_exposure_inr"], 0)

    def test_data_quality_detects_stale_missing_and_negative_records(self):
        """INV-04/05: stale syncs, missing sources, and negative channel stock are flagged."""
        result = self.tool.invoke({"action": "data_quality"}, {})
        issues = [item["issue"] for item in result["issues"]]
        self.assertIn("STALE_SYNC", issues)
        self.assertIn("MISSING_SYNC", issues)
        self.assertIn("NEGATIVE_CHANNEL_INVENTORY", issues)

    def test_trace_sku_explains_negative_balance(self):
        """INV-05: the ledger trace identifies the first event that caused a negative balance."""
        result = self.tool.invoke({"action": "trace_sku", "sku": "SKU-006"}, {})
        self.assertTrue(result["negative_balance_detected"])
        self.assertEqual(result["final_ledger_balance"], -3)
        self.assertIsNotNone(result["first_negative_event"])

    def test_committed_stock_reports_free_units(self):
        """INV-06: commitments are reported without subtracting them twice."""
        result = self.tool.invoke({"action": "committed_stock"}, {})
        item = next(entry for entry in result["items"] if entry["sku"] == "SKU-001")
        self.assertEqual(item["committed_to_orders"], 6)
        self.assertEqual(item["free_after_commitments"], 14)

    def test_trust_report_marks_low_confidence_sources(self):
        """INV-08: conflicting sources reduce trust and remain visible."""
        result = self.tool.invoke({"action": "trust_report"}, {})
        by_sku = {item["sku"]: item for item in result["items"]}
        self.assertLess(by_sku["SKU-006"]["trust_score"], 50)
        self.assertEqual(by_sku["SKU-006"]["confidence"], "LOW")
        self.assertTrue(by_sku["SKU-006"]["unresolved_differences"])


if __name__ == "__main__":
    unittest.main()
