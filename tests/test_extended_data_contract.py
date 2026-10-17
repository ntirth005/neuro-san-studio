"""
Data contract tests for the extended inventory operations datasets.
"""

import unittest

from coded_tools.inventory_ops.data_store import InventoryDataStore


class TestExtendedDataContract(unittest.TestCase):
    """Verify the additive files and seeded anomalies are present."""

    def setUp(self) -> None:
        self.store = InventoryDataStore()

    def test_extended_files_are_present(self) -> None:
        expected = [
            "location_demand.csv",
            "fulfillment_capacity.csv",
            "transfer_lanes.csv",
            "transfers.csv",
            "customer_orders.csv",
            "order_allocations.csv",
            "sellability_rules.csv",
            "inventory_batches.csv",
            "quality_events.csv",
            "recalls.csv",
            "inventory_counts.csv",
            "freight_costs.csv",
            "policy_history.csv",
            "inventory_snapshots.csv",
            "approvals.csv",
            "recommendation_history.csv",
            "action_log.csv",
            "integration_health.csv",
        ]
        for filename in expected:
            self.assertGreater(len(self.store.load(filename)), 0, filename)

    def test_location_demand_covers_ninety_days(self) -> None:
        self.assertEqual(len(self.store.location_demand()), 1620)
        dates = {row["date"] for row in self.store.location_demand()}
        self.assertEqual(len(dates), 90)

    def test_batch_totals_reconcile_to_warehouse_stock(self) -> None:
        total = sum(
            self.store.to_float(row["quantity"])
            for row in self.store.inventory_batches()
            if row["sku"] == "SKU-001" and row["location_id"] == "WH-01"
        )
        self.assertEqual(total, 12)

    def test_active_recall_and_channel_exposure(self) -> None:
        recalls = [row for row in self.store.recalls() if row["status"] == "active"]
        self.assertEqual(len(recalls), 1)
        self.assertEqual(recalls[0]["sku"], "SKU-003")

    def test_delayed_transfer_exists(self) -> None:
        delayed = [row for row in self.store.transfers() if row["status"] == "delayed"]
        self.assertEqual(len(delayed), 1)
        self.assertEqual(delayed[0]["transfer_id"], "TRF-102")

    def test_approval_and_integration_anomalies_exist(self) -> None:
        pending = [row for row in self.store.approvals() if row["status"] == "pending"]
        self.assertGreaterEqual(len(pending), 3)
        down = [row for row in self.store.integration_health() if row["status"] == "down"]
        self.assertEqual(down[0]["system"], "marketplace_api")


if __name__ == "__main__":
    unittest.main()
