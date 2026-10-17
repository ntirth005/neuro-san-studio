"""
Unit tests for the daily operations and audit tool.
"""

import unittest
import uuid

from coded_tools.inventory_ops.operations_control_tool import OperationsControlTool


class TestOperationsControlTool(unittest.TestCase):
    """Verify OPS question coverage, idempotency, and approval guardrails."""

    def setUp(self) -> None:
        self.tool = OperationsControlTool()
        self.key = f"test-{uuid.uuid4().hex}"

    def test_daily_exception_queue_returns_top_five(self) -> None:
        result = self.tool.invoke({"action": "daily_exception_queue", "limit": 5}, {})
        self.assertEqual(len(result["items"]), 5)
        self.assertGreaterEqual(result["summary"]["critical"], 1)
        self.assertEqual(result["items"][0]["rank"], 1)

    def test_change_since_yesterday_reports_material_changes(self) -> None:
        result = self.tool.invoke({"action": "change_since_yesterday"}, {})
        self.assertGreater(result["summary"]["material_inventory_changes"], 0)
        self.assertGreater(result["summary"]["new_operational_events"], 0)

    def test_pending_approvals_escalates_overdue(self) -> None:
        result = self.tool.invoke({"action": "pending_approvals"}, {})
        self.assertGreaterEqual(result["summary"]["overdue"], 1)
        self.assertGreaterEqual(result["summary"]["level_3"], 1)

    def test_recommendation_history_explains_change(self) -> None:
        result = self.tool.invoke({"action": "recommendation_history", "sku": "SKU-003"}, {})
        self.assertGreaterEqual(result["summary"]["comparisons"], 1)
        self.assertIn("change_reason", result["comparisons"][0])

    def test_integration_health_enters_safe_mode(self) -> None:
        result = self.tool.invoke({"action": "integration_health"}, {})
        self.assertTrue(result["summary"]["safe_mode"])
        self.assertIn("marketplace_listing_update", result["blocked_actions"])

    def test_create_po_draft_is_idempotent(self) -> None:
        args = {
            "action": "create_po_draft",
            "order_items": [{"sku": "SKU-001", "quantity": 20}],
            "budget_limit": 10000,
            "idempotency_key": self.key,
        }
        first = self.tool.invoke(args, {})
        self.assertFalse(first["idempotent_replay"])
        self.assertEqual(first["draft"]["total_amount"], 5000)
        second = self.tool.invoke(args, {})
        self.assertTrue(second["idempotent_replay"])

    def test_create_po_draft_rejects_budget_overrun(self) -> None:
        result = self.tool.invoke(
            {
                "action": "create_po_draft",
                "order_items": [{"sku": "SKU-003", "quantity": 10}],
                "budget_limit": 1000,
                "idempotency_key": self.key,
            },
            {},
        )
        self.assertIn("error", result)

    def test_send_po_blocks_without_approval(self) -> None:
        result = self.tool.invoke({"action": "send_po", "po_id": "PO-2026-1001"}, {})
        self.assertFalse(result["sent"])
        self.assertTrue(result["blocked"])
        self.assertTrue(result["reasons"])

    def test_send_po_succeeds_with_approval(self) -> None:
        result = self.tool.invoke({"action": "send_po", "po_id": "PO-2026-1006"}, {})
        self.assertTrue(result["sent"])
        self.assertFalse(result["external_side_effect"])

    def test_reverse_action_preserves_audit_trail(self) -> None:
        result = self.tool.invoke({"action": "reverse_action", "action_id": "ACT-001"}, {})
        reversal = result.get("reversal", {})
        self.assertEqual(reversal.get("reversal_of"), "ACT-001")
        self.assertIn(reversal.get("status"), {"completed", "pending", "blocked"})

    def test_explain_recommendation_shows_evidence(self) -> None:
        result = self.tool.invoke({"action": "explain_recommendation", "sku": "SKU-003"}, {})
        self.assertTrue(result["evidence"])
        self.assertTrue(result["alternatives"])
        self.assertIsNotNone(result["why_changed"])

    def test_action_status_reports_completion_states(self) -> None:
        result = self.tool.invoke({"action": "action_status"}, {})
        self.assertGreater(result["summary"]["actions"], 0)
        self.assertGreaterEqual(result["summary"]["completed"], 1)


if __name__ == "__main__":
    unittest.main()
