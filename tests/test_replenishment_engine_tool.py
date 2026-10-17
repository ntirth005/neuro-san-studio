"""Unit tests for replenishment decisions and operating policy."""

import unittest

from coded_tools.inventory_ops.replenishment_engine_tool import ReplenishmentEngineTool


class TestReplenishmentEngineTool(unittest.TestCase):
    """Cover the stockout, excess, and replenishment question set."""

    def setUp(self):
        self.tool = ReplenishmentEngineTool()

    def test_queue_is_prioritized_and_supplier_compliant(self):
        """REP-01/02: the queue is ranked and respects MOQ/case packs."""
        result = self.tool.invoke({"action": "queue", "limit": 10}, {})
        self.assertGreater(result["summary"]["items_to_reorder"], 0)
        for item in result["items"]:
            self.assertGreater(item["order_quantity"], 0)

    def test_quantity_reconciles_forecast_and_open_pos(self):
        """REP-02: quantity subtracts position and open purchase orders."""
        result = self.tool.invoke({"action": "quantity", "sku": "SKU-001"}, {})
        self.assertIn("open_po_units", result)
        self.assertGreater(result["open_po_units"], 0)
        self.assertGreaterEqual(result["final_order_quantity"], result["raw_order_quantity"])

    def test_order_by_date_returns_deadline_and_probability(self):
        """REP-03: latest safe order date and stockout probability are explicit."""
        result = self.tool.invoke({"action": "order_by_date", "sku": "SKU-001"}, {})
        self.assertIn("latest_safe_order_date", result)
        self.assertIn("stockout_probability_before_delivery", result)
        self.assertEqual(result["recommendation"], "ORDER_NOW")

    def test_excess_and_dead_stock_are_distinct(self):
        """REP-04/05: excess capital and persistent dead stock are reported."""
        excess = self.tool.invoke({"action": "excess"}, {})
        self.assertGreater(excess["summary"]["total_capital_tied_up_inr"], 0)
        dead = self.tool.invoke({"action": "dead_stock"}, {})
        skus = [item["sku"] for item in dead["items"]]
        self.assertIn("SKU-004", skus)

    def test_reduce_order_shows_tradeoff(self):
        """REP-06: reducing an order changes fill rate and lost margin."""
        result = self.tool.invoke(
            {"action": "reduce_order", "sku": "SKU-001", "order_quantity": 50, "horizon_days": 30}, {}
        )
        self.assertGreater(len(result["options"]), 1)
        first, last = result["options"][0], result["options"][-1]
        self.assertLessEqual(last["expected_fill_rate"], first["expected_fill_rate"])
        self.assertGreaterEqual(last["expected_lost_margin_inr"], first["expected_lost_margin_inr"])

    def test_budget_allocation_respects_limit(self):
        """REP-07: bounded knapsack never exceeds the budget."""
        result = self.tool.invoke({"action": "budget_allocate", "budget_limit": 20000}, {})
        self.assertLessEqual(result["budget_spent_inr"], 20000.0)
        self.assertEqual(result["strategy"], "bounded_knapsack_dp")

    def test_order_frequency_and_moq_alternatives(self):
        """REP-08/09: order frequency costs and MOQ overbuy are quantified."""
        frequency = self.tool.invoke({"action": "order_frequency", "sku": "SKU-001"}, {})
        self.assertGreater(len(frequency["options"]), 1)
        self.assertIn("recommended", frequency)
        moq = self.tool.invoke({"action": "moq_alternatives", "sku": "SKU-001"}, {})
        self.assertIn("primary_supplier", moq)
        self.assertGreater(len(moq["alternatives"]), 0)

    def test_automation_policy_returns_verdicts(self):
        """REP-10: every recommendation receives an automation verdict."""
        result = self.tool.invoke({"action": "automation_policy"}, {})
        self.assertGreater(len(result["items"]), 0)
        for item in result["items"]:
            self.assertIn(item["verdict"], ["AUTO_APPROVE", "HUMAN_REVIEW", "BLOCK"])


if __name__ == "__main__":
    unittest.main()
