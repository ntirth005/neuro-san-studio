"""Unit tests for purchase-order lifecycle and supplier sourcing."""

import unittest

from coded_tools.inventory_ops.purchase_order_lifecycle_tool import PurchaseOrderLifecycleTool
from coded_tools.inventory_ops.supplier_sourcing_tool import SupplierSourcingTool


class TestSupplierCoordinationTools(unittest.TestCase):
    """Cover the supplier coordination and purchase-order lifecycle question set."""

    def setUp(self):
        self.lifecycle = PurchaseOrderLifecycleTool()
        self.sourcing = SupplierSourcingTool()

    def test_exception_queue_and_confirmation_status(self):
        """SUP-01/02: overdue acknowledgements and requested-vs-confirmed dates are visible."""
        exceptions = self.lifecycle.invoke({"action": "exception_queue"}, {})
        po_ids = [item["po_id"] for item in exceptions["exceptions"]]
        self.assertIn("PO-2026-1001", po_ids)
        self.assertIn("PO-2026-1002", po_ids)
        confirmation = self.lifecycle.invoke({"action": "confirmation_status", "po_id": "PO-2026-1002"}, {})
        self.assertTrue(confirmation["date_confirmed_by_supplier"])
        self.assertNotEqual(confirmation["requested_date"], confirmation["confirmed_date"])

    def test_scorecard_and_shortfall_options(self):
        """SUP-03/04: reliability comes from receipts and shortfall options are compared."""
        scorecard = self.sourcing.invoke({"action": "supplier_scorecard"}, {})
        self.assertGreater(scorecard["summary"]["suppliers_evaluated"], 0)
        self.assertTrue(any(card["receipts_observed"] > 0 for card in scorecard["scorecards"]))
        shortfall = self.sourcing.invoke(
            {"action": "shortfall_options", "po_id": "PO-2026-1003", "available_qty": 7}, {}
        )
        self.assertEqual(shortfall["shortfall_units"], 5)
        self.assertGreaterEqual(len(shortfall["options"]), 2)

    def test_delay_impact_and_price_change(self):
        """SUP-05/07: delays recalculate risk and price changes compare alternatives."""
        delay = self.sourcing.invoke({"action": "delay_impact", "po_id": "PO-2026-1002", "delay_days": 14}, {})
        self.assertGreater(delay["stockout_gap_days"], 0)
        self.assertGreater(delay["expected_lost_gmv_inr"], 0)
        price = self.sourcing.invoke(
            {"action": "price_change_eval", "po_id": "PO-2026-1005", "new_unit_cost": 495}, {}
        )
        self.assertEqual(price["old_unit_cost"], 450.0)
        self.assertIn(price["recommendation"], ["ACCEPT", "NEGOTIATE", "CONSIDER_ALTERNATE", "REJECT_OR_RENEGOTIATE"])

    def test_followup_is_approval_gated(self):
        """SUP-06: follow-up drafting never sends without approval."""
        result = self.lifecycle.invoke({"action": "draft_followup", "po_id": "PO-2026-1001"}, {})
        self.assertTrue(result["requires_human_approval"])
        self.assertFalse(result["sent"])
        self.assertIn("PO-2026-1001", result["subject"])

    def test_alternates_and_failure_simulation(self):
        """SUP-08/12: approved alternates are ranked and failure impact is recalculated."""
        alternates = self.sourcing.invoke({"action": "alternate_suppliers", "sku": "SKU-001", "quantity": 20}, {})
        self.assertGreater(len(alternates["alternates"]), 0)
        failure = self.sourcing.invoke({"action": "supplier_failure_sim", "supplier_id": "SUP-003"}, {})
        self.assertGreater(failure["affected_purchase_orders"], 0)

    def test_escalation_receipts_and_partial_fulfillment(self):
        """SUP-09/10/11: escalations, receipt reconciliation, and backorders are tracked."""
        escalation = self.lifecycle.invoke({"action": "escalation_queue"}, {})
        self.assertIn("PO-2026-1001", [item["po_id"] for item in escalation["items"]])
        receipts = self.lifecycle.invoke({"action": "receipt_reconciliation"}, {})
        by_po = {item["po_id"]: item for item in receipts["items"]}
        self.assertEqual(by_po["PO-2026-1003"]["reconciliation_status"], "SHORT")
        partial = self.lifecycle.invoke({"action": "partial_fulfillment"}, {})
        self.assertGreater(partial["summary"]["total_backorder_units"], 0)


if __name__ == "__main__":
    unittest.main()
