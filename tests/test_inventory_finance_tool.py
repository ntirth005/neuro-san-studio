"""
Unit tests for the inventory finance tool.
"""

import unittest

from coded_tools.inventory_ops.inventory_finance_tool import InventoryFinanceTool


class TestInventoryFinanceTool(unittest.TestCase):
    """Verify FIN question coverage and assumption transparency."""

    def setUp(self) -> None:
        self.tool = InventoryFinanceTool()

    def test_capital_tied_up_uses_cost_basis(self) -> None:
        result = self.tool.invoke({"action": "capital_tied_up"}, {})
        self.assertGreater(result["summary"]["total_inventory_value_inr"], 0)
        self.assertIn("unit cost", result["accounting_basis"])
        self.assertGreater(result["summary"]["unusable_value_inr"], 0)

    def test_gross_profit_ranking_separates_gross_and_net(self) -> None:
        result = self.tool.invoke({"action": "gross_profit_ranking", "horizon_days": 90}, {})
        self.assertEqual(result["summary"]["highest_gross_profit"]["sku"], "SKU-003")
        self.assertIn("gross_profit_inr", result["items"][0])
        self.assertIn("net_profit_inr", result["items"][0])

    def test_excess_carrying_cost_has_assumptions(self) -> None:
        result = self.tool.invoke({"action": "excess_carrying_cost", "period_days": 30}, {})
        self.assertGreater(result["summary"]["annual_carrying_cost_inr"], 0)
        self.assertIn("holding_rate_pct", result["assumptions"])

    def test_stockout_impact_reports_lost_margin(self) -> None:
        result = self.tool.invoke({"action": "stockout_impact", "horizon_days": 30}, {})
        self.assertGreater(result["summary"]["total_projected_lost_margin_inr"], 0)
        self.assertTrue(result["assumptions"])

    def test_budget_vs_hold_compares_scenarios(self) -> None:
        result = self.tool.invoke({"action": "budget_vs_hold", "budget_limit": 30000, "horizon_days": 30}, {})
        self.assertLessEqual(result["replenishment_scenario"]["spend_inr"], 30000)
        self.assertIn("recommendation", result)

    def test_supplier_cost_impact_ranks_landed_extras(self) -> None:
        result = self.tool.invoke({"action": "supplier_cost_impact"}, {})
        self.assertEqual(result["summary"]["highest_landed_extra"]["supplier_id"], "SUP-003")
        self.assertGreater(result["summary"]["total_landed_extras_inr"], 0)

    def test_service_level_simulation_reduces_service(self) -> None:
        result = self.tool.invoke({"action": "service_level_simulation", "reduction_percent": 10}, {})
        self.assertLess(
            result["summary"]["weighted_service_level_after_pct"],
            result["summary"]["weighted_service_level_before_pct"],
        )
        self.assertGreater(result["summary"]["investment_reduction_inr"], 0)

    def test_policy_savings_shows_confounders(self) -> None:
        result = self.tool.invoke({"action": "policy_savings", "period_days": 30}, {})
        self.assertGreater(result["financials"]["estimated_savings_inr"], 0)
        self.assertTrue(result["confounders"])
        self.assertEqual(result["confidence"], "MEDIUM")


if __name__ == "__main__":
    unittest.main()
