"""
Unit tests for the advanced enterprise supply chain & inventory operations suite:
- InventoryAnalyticsTool (uncensored demand, outlier filtering, GMROI)
- KnapsackBudgetOptimizerTool (bounded knapsack profit maximization)
- DeadStockLiquidationTool (trapped working capital & markdown clearance)
- ScenarioSimulationTool (lead-time shocks & demand surges)
- ABCClassificationTool (dual-axis ABC/XYZ revenue & predictability segmentation)
- ReturnPipelineTool (reverse logistics, RTO pipeline, True Net Velocity)
- SupplierReliabilityTool (vendor scorecards, lead-time jitter sigma_LT, stochastic safety stock)
"""

import os
import sys
import unittest

# Ensure repo root is on sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool
from coded_tools.inventory_ops.knapsack_budget_optimizer_tool import KnapsackBudgetOptimizerTool
from coded_tools.inventory_ops.dead_stock_liquidation_tool import DeadStockLiquidationTool
from coded_tools.inventory_ops.scenario_simulation_tool import ScenarioSimulationTool
from coded_tools.inventory_ops.abc_classification_tool import ABCClassificationTool
from coded_tools.inventory_ops.returns_pipeline_tool import ReturnPipelineTool
from coded_tools.inventory_ops.supplier_reliability_tool import SupplierReliabilityTool


class TestAdvancedInventoryOps(unittest.TestCase):
    """Test suite covering edge cases and advanced inventory operations."""

    def setUp(self):
        self.analytics_tool = InventoryAnalyticsTool()
        self.knapsack_tool = KnapsackBudgetOptimizerTool()
        self.liquidation_tool = DeadStockLiquidationTool()
        self.simulation_tool = ScenarioSimulationTool()
        self.abc_tool = ABCClassificationTool()
        self.return_tool = ReturnPipelineTool()
        self.supplier_rel_tool = SupplierReliabilityTool()

    def test_01_uncensored_demand_and_gmroi(self):
        """Verify uncensored demand, outlier filtering, and GMROI financial metrics."""
        res = self.analytics_tool.invoke({"sku": "SKU-001"}, {})
        self.assertIn("sku_analysis", res)
        analysis = res["sku_analysis"]

        # Check financial efficiency
        self.assertIn("financial_efficiency", analysis)
        self.assertGreater(analysis["financial_efficiency"]["daily_gross_profit_contribution"], 0)
        self.assertGreater(analysis["financial_efficiency"]["gmroi_capital_index"], 0)

        # Check unit gross margin
        self.assertEqual(analysis["unit_gross_margin"], 249.0)  # 499 - 250
        self.assertGreater(analysis["metrics"]["daily_sales_velocity"], 0)

    def test_02_knapsack_budget_optimizer(self):
        """Verify bounded knapsack budget allocation under tight working capital limit."""
        res = self.knapsack_tool.invoke({"budget_limit": 20000.0}, {})
        self.assertIn("optimal_allocation", res)
        self.assertIn("financial_optimization_benchmark", res)

        spent = res["budget_spent"]
        self.assertLessEqual(spent, 20000.0)
        self.assertGreater(spent, 0.0)

        allocated_items = res["optimal_allocation"]["items"]
        self.assertTrue(len(allocated_items) > 0)
        for it in allocated_items:
            self.assertGreaterEqual(it["recommended_units"], it["moq"])

        benchmark = res["financial_optimization_benchmark"]
        self.assertGreaterEqual(
            benchmark["optimal_profit_protected_inr"],
            benchmark["naive_fifo_profit_protected_inr"],
        )

    def test_03_dead_stock_liquidation(self):
        """Verify identification of slow-moving inventory and trapped capital calculation."""
        res = self.liquidation_tool.invoke({"dos_threshold": 60.0, "discount_percent": 20.0}, {})
        self.assertIn("dead_stock_audit_summary", res)
        summary = res["dead_stock_audit_summary"]

        self.assertGreater(summary["total_trapped_capital_inr"], 0.0)
        self.assertGreater(summary["total_projected_cash_unlock_inr"], 0.0)

        overstocked_skus = [it["sku"] for it in res["overstocked_items"]]
        self.assertIn("SKU-004", overstocked_skus)

    def test_04_scenario_simulation_lead_time_shock(self):
        """Verify supply chain shock simulation with +7 day lead time delay."""
        res = self.simulation_tool.invoke({"lead_time_shock_days": 7.0, "demand_surge_percent": 30.0}, {})
        self.assertIn("impact_summary", res)
        impact = res["impact_summary"]

        self.assertGreater(impact["skus_facing_stockout"], 0)
        self.assertGreater(impact["total_projected_lost_gmv_inr"], 0.0)
        self.assertIsNotNone(impact["first_to_fail_sku"])

        timeline = res["sku_stress_timeline"]
        for i in range(len(timeline) - 1):
            self.assertLessEqual(timeline[i]["stressed_days_of_supply"], timeline[i + 1]["stressed_days_of_supply"])

    def test_05_abc_xyz_portfolio_classification(self):
        """Verify dual-axis ABC/XYZ portfolio classification."""
        res = self.abc_tool.invoke({}, {})
        self.assertIn("portfolio_summary", res)
        self.assertIn("classified_skus", res)

        summary = res["portfolio_summary"]
        self.assertGreater(summary["total_skus_classified"], 0)
        self.assertGreater(summary["class_A_count"], 0)

        classified = res["classified_skus"]
        for item in classified:
            self.assertIn(item["abc_class"], ["A", "B", "C"])
            self.assertIn(item["xyz_class"], ["X", "Y", "Z"])
            self.assertIn("strategic_recommendation", item)

    def test_06_return_rto_pipeline(self):
        """Verify return and RTO pipeline analytics and true net velocity."""
        res = self.return_tool.invoke({}, {})
        self.assertIn("reverse_logistics_summary", res)
        self.assertIn("sku_return_analytics", res)

        summary = res["reverse_logistics_summary"]
        self.assertGreater(summary["total_returns_logged"], 0)
        self.assertGreater(summary["in_transit_rto_units_pipeline"], 0)

        sku_list = res["sku_return_analytics"]
        sku_001 = next((s for s in sku_list if s["sku"] == "SKU-001"), None)
        self.assertIsNotNone(sku_001)
        # Net velocity should be less than or equal to gross velocity
        self.assertLessEqual(
            sku_001["velocity_adjustment"]["true_net_daily_velocity"],
            sku_001["gross_daily_velocity"],
        )
        self.assertGreater(sku_001["procurement_impact"]["incoming_rto_stock_offset"], 0)

    def test_07_supplier_reliability_and_stochastic_safety_stock(self):
        """Verify vendor fulfillment scoring, lead-time jitter, and stochastic safety stock."""
        res = self.supplier_rel_tool.invoke({}, {})
        self.assertIn("supplier_audit_summary", res)
        self.assertIn("vendor_scorecards", res)

        scorecards = res["vendor_scorecards"]
        self.assertGreater(len(scorecards), 0)

        for card in scorecards:
            perf = card["fulfillment_performance"]
            self.assertGreater(perf["reliability_score_out_of_100"], 0)
            self.assertIn(
                perf["vendor_grade"],
                ["TIER-1 (EXCELLENT)", "TIER-2 (ACCEPTABLE)", "HIGH_RISK (VULNERABLE)"],
            )

            stochastic = card["stochastic_safety_stock"]
            # Lead-time jitter makes stochastic safety stock at least the naive buffer.
            self.assertGreaterEqual(
                stochastic["stochastic_safety_stock_units"],
                stochastic["naive_static_safety_stock_units"],
            )


if __name__ == "__main__":
    unittest.main()
