"""Unit tests for demand forecasting, uncertainty, and reliability."""

import unittest

from coded_tools.inventory_ops.demand_forecast_tool import DemandForecastTool


class TestDemandForecastTool(unittest.TestCase):
    """Cover the demand forecasting and uncertainty question set."""

    def setUp(self):
        self.tool = DemandForecastTool()

    def test_forecast_returns_units_intervals_and_model(self):
        """DEM-02: forecast includes uncertainty and model provenance."""
        result = self.tool.invoke({"action": "forecast", "sku": "SKU-001", "horizon_days": 30}, {})
        self.assertGreater(result["forecast_units"], 0)
        self.assertLessEqual(result["interval_80"][0], result["forecast_units"])
        self.assertGreaterEqual(result["interval_80"][1], result["forecast_units"])
        self.assertEqual(result["confidence"], "HIGH")
        self.assertIn("backtest", result)

    def test_stockout_horizon_prioritizes_risk(self):
        """DEM-01: at-risk SKUs are ranked by expected lost GMV."""
        result = self.tool.invoke({"action": "stockout_horizon", "horizon_days": 14}, {})
        self.assertGreater(result["summary"]["at_risk_skus"], 0)
        self.assertGreater(result["summary"]["total_expected_lost_gmv_inr"], 0)
        skus = [item["sku"] for item in result["items"]]
        self.assertIn("SKU-001", skus)

    def test_explain_change_and_spike_classification(self):
        """DEM-03/04: demand changes expose drivers and spikes are classified."""
        explanation = self.tool.invoke({"action": "explain_change", "sku": "SKU-001"}, {})
        self.assertIn("change_percent", explanation)
        self.assertIn("weekday_index", explanation)
        classification = self.tool.invoke({"action": "classify_spike", "sku": "SKU-001"}, {})
        self.assertIn(
            classification["classification"], ["NORMAL", "ONE_OFF_OR_UNCERTAIN", "SUSTAINED_SHIFT", "SPARSE_HISTORY"]
        )

    def test_promotion_scenario_recalculates_stockout(self):
        """DEM-05: a demand uplift produces a stockout gap and buffer requirement."""
        result = self.tool.invoke(
            {"action": "promotion_scenario", "sku": "SKU-001", "uplift_percent": 100, "horizon_days": 14}, {}
        )
        self.assertGreater(result["scenario_daily_average"], result["net_position"] / 14)
        self.assertGreater(result["mitigation_buffer_units"], 0)

    def test_new_product_uses_labelled_fallback(self):
        """DEM-06: a new SKU returns an explicit low-confidence fallback."""
        result = self.tool.invoke({"action": "new_product_forecast", "sku": "SKU-006", "horizon_days": 30}, {})
        self.assertEqual(result["confidence"], "INSUFFICIENT_HISTORY")
        self.assertIn(result["model"], ["comparable_category", "category_default"])

    def test_backtest_and_reliability(self):
        """DEM-07/08: backtest metrics and automation reliability flags are produced."""
        backtest = self.tool.invoke({"action": "backtest", "sku": "SKU-001"}, {})
        self.assertTrue(backtest["backtest"]["available"])
        self.assertIn("wape", backtest["backtest"])
        reliability = self.tool.invoke({"action": "reliability"}, {})
        self.assertGreater(reliability["summary"]["skus_reviewed"], 0)
        self.assertGreater(reliability["summary"]["not_automatable"], 0)


if __name__ == "__main__":
    unittest.main()
