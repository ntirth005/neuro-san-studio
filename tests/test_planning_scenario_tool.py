"""
Unit tests for the planning scenario tool.
"""

import unittest

from coded_tools.inventory_ops.planning_scenario_tool import PlanningScenarioTool


class TestPlanningScenarioTool(unittest.TestCase):
    """Verify SIM question coverage and scenario assumptions."""

    def setUp(self) -> None:
        self.tool = PlanningScenarioTool()

    def test_demand_surge_recomputes_shortage(self) -> None:
        result = self.tool.invoke({"action": "demand_surge", "demand_change_percent": 20, "horizon_days": 30}, {})
        self.assertGreater(result["summary"]["total_shortage_units"], 0)
        self.assertGreater(result["summary"]["total_purchase_cost_inr"], 0)

    def test_lead_time_double_increases_position_need(self) -> None:
        result = self.tool.invoke({"action": "lead_time_double", "lead_time_multiplier": 2, "horizon_days": 30}, {})
        self.assertGreater(result["summary"]["skus_needing_more_position"], 0)
        self.assertGreater(result["summary"]["total_additional_units_needed"], 0)

    def test_budget_cut_defers_items(self) -> None:
        result = self.tool.invoke({"action": "budget_cut", "budget_change_percent": -30, "budget_limit": 30000}, {})
        self.assertLess(result["revised_budget_inr"], result["original_budget_inr"])
        self.assertGreater(result["summary"]["deferred_items"], 0)

    def test_discontinue_slow_finds_slow_movers(self) -> None:
        result = self.tool.invoke({"action": "discontinue_slow", "limit": 2}, {})
        self.assertIn("SKU-006", result["summary"]["slowest_products"])
        self.assertGreater(result["summary"]["trapped_capital_in_slow_movers_inr"], 0)

    def test_wholesale_order_can_be_fulfilled(self) -> None:
        result = self.tool.invoke({"action": "wholesale_order", "sku": "SKU-004", "quantity": 100}, {})
        self.assertTrue(result["summary"]["feasible_now"])
        self.assertGreater(result["summary"]["gross_margin_if_fulfilled_inr"], 0)

    def test_seasonal_plan_builds_purchase_plan(self) -> None:
        result = self.tool.invoke({"action": "seasonal_plan", "horizon_days": 30, "uplift_percent": 20}, {})
        self.assertGreater(result["summary"]["total_purchase_cost_inr"], 0)
        self.assertGreater(result["summary"]["skus_needing_stock"], 0)

    def test_service_vs_cost_returns_frontier(self) -> None:
        result = self.tool.invoke({"action": "service_vs_cost"}, {})
        self.assertEqual(len(result["scenarios"]), 3)
        investments = [item["safety_stock_investment_inr"] for item in result["scenarios"]]
        self.assertEqual(investments, sorted(investments))

    def test_service_level_simulation_delegates_to_finance(self) -> None:
        result = self.tool.invoke({"action": "service_level_simulation", "reduction_percent": 10}, {})
        self.assertEqual(result["scenario"], "service_level_simulation")
        self.assertLess(
            result["summary"]["weighted_service_level_after_pct"],
            result["summary"]["weighted_service_level_before_pct"],
        )

    def test_supplier_failure_ranks_contingency(self) -> None:
        result = self.tool.invoke({"action": "supplier_failure", "supplier_id": "SUP-003"}, {})
        self.assertEqual(result["summary"]["first_shortage_sku"], "SKU-003")
        self.assertGreater(result["summary"]["skus_with_alternate"], 0)
        self.assertLess(result["summary"]["days_to_first_shortage"], 14)


if __name__ == "__main__":
    unittest.main()
