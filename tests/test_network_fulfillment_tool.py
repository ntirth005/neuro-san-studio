"""
Unit tests for the multi-location fulfillment tool.
"""

import unittest

from coded_tools.inventory_ops.network_fulfillment_tool import NetworkFulfillmentTool


class TestNetworkFulfillmentTool(unittest.TestCase):
    """Verify LOC question coverage and guardrails."""

    def setUp(self) -> None:
        self.tool = NetworkFulfillmentTool()

    def test_fulfill_options_prefers_transfer_for_order(self) -> None:
        result = self.tool.invoke({"action": "fulfill_options", "order_id": "ORD-LOC-9001"}, {})
        self.assertTrue(result["feasible_from_network"])
        self.assertEqual(result["unfulfilled_units"], 0)
        self.assertGreaterEqual(sum(item["allocated_units"] for item in result["fulfillment_plan"]), 12)

    def test_location_stockout_risk_ranks_first_failure(self) -> None:
        result = self.tool.invoke({"action": "location_stockout_risk", "horizon_days": 14}, {})
        self.assertGreater(result["summary"]["critical"], 0)
        self.assertIn(result["summary"]["first_to_run_out"]["risk"], {"CRITICAL", "WARNING"})

    def test_transfer_candidates_find_excess_to_shortage(self) -> None:
        result = self.tool.invoke({"action": "transfer_candidates", "sku": "SKU-002"}, {})
        self.assertGreater(result["summary"]["transfer_candidates"], 0)
        self.assertGreater(result["summary"]["total_units"], 0)

    def test_in_transit_detects_delayed_transfer(self) -> None:
        result = self.tool.invoke({"action": "in_transit"}, {})
        self.assertGreaterEqual(result["summary"]["delayed_transfers"], 1)
        self.assertGreater(result["summary"]["in_transit_or_planned_units"], 0)

    def test_sellability_check_blocks_unmapped_channel(self) -> None:
        result = self.tool.invoke({"action": "sellability_check", "sku": "SKU-006", "channel": "storefront"}, {})
        self.assertEqual(result["summary"]["physically_available"], 30)
        warehouse_rules = [
            rule
            for location in result["physical_locations"]
            if location["location_id"] == "WH-01"
            for rule in location["channel_rules"]
        ]
        self.assertTrue(warehouse_rules)
        self.assertFalse(warehouse_rules[0]["eligible_location"])

    def test_allocation_conflicts_detect_over_allocation(self) -> None:
        result = self.tool.invoke({"action": "allocation_conflicts"}, {})
        order_ids = {item["order_id"] for item in result["conflicts"]}
        self.assertIn("ORD-LOC-9005", order_ids)
        self.assertGreater(result["summary"]["financial_exposure_inr"], 0)

    def test_redistribute_plan_proposes_transfers(self) -> None:
        result = self.tool.invoke({"action": "redistribute_plan", "horizon_days": 30}, {})
        self.assertGreater(result["summary"]["recommended_transfers"], 0)

    def test_closure_simulation_projects_shortfall(self) -> None:
        result = self.tool.invoke({"action": "closure_simulation", "location_id": "WH-01", "horizon_days": 30}, {})
        self.assertTrue(result["summary"]["capacity_sufficient"])
        self.assertGreater(result["summary"]["projected_lost_units"], 0)


if __name__ == "__main__":
    unittest.main()
