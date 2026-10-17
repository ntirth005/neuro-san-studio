"""
Inventory reconciliation CodedTool for Neuro-SAN.
Reconciles warehouse, storefront, and marketplace records, detects stale data,
traces ledger movements, and scores source trust.
"""

import asyncio
import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore

logger = logging.getLogger(__name__)


class InventoryReconciliationTool(CodedTool):
    """
    CodedTool that answers inventory visibility, integrity, and trust questions.
    """

    def __init__(self) -> None:
        """Initialize the tool with the shared data store."""
        self.store = InventoryDataStore()
        logger.info("%s initialized.", self.__class__.__name__)

    def _available_to_sell(self, sku: Optional[str] = None) -> Dict[str, Any]:
        """Calculate sellable stock per SKU and location, keeping incoming separate."""
        products = self.store.products()
        grouped = self.store.inventory_by_sku()
        items = []
        total_available = 0.0
        total_incoming = 0.0
        for product_sku, product in products.items():
            if sku and product_sku != sku:
                continue
            locations = []
            available = 0.0
            incoming = 0.0
            for row in grouped.get(product_sku, []):
                on_hand = self.store.to_float(row.get("on_hand_qty"))
                reserved = self.store.to_float(row.get("reserved_qty"))
                unavailable = self.store.to_float(row.get("unavailable_qty"))
                location_incoming = self.store.to_float(row.get("incoming_qty"))
                location_available = max(0.0, on_hand - reserved - unavailable)
                available += location_available
                incoming += location_incoming
                locations.append(
                    {
                        "location_id": row.get("location_id"),
                        "on_hand": int(on_hand),
                        "reserved": int(reserved),
                        "unavailable": int(unavailable),
                        "incoming": int(location_incoming),
                        "available_to_sell": int(location_available),
                        "snapshot_at": row.get("snapshot_at"),
                    }
                )
            total_available += available
            total_incoming += incoming
            items.append(
                {
                    "sku": product_sku,
                    "product_name": product.get("product_name"),
                    "available_to_sell": int(available),
                    "incoming_not_sellable": int(incoming),
                    "locations": locations,
                }
            )
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "skus": len(items),
                "total_available_to_sell": int(total_available),
                "total_incoming_not_sellable": int(total_incoming),
            },
            "items": items,
        }

    def _source_discrepancies(self) -> Dict[str, Any]:
        """Compare warehouse availability with channel records and detect overlaps."""
        products = self.store.products()
        available = {item["sku"]: item for item in self._available_to_sell()["items"]}
        locations = {row.get("location_id", ""): row for row in self.store.load("inventory_locations.csv")}
        channel_rows = self.store.load("channel_inventory.csv")
        warehouse_locations = self.store.inventory_by_sku()

        discrepancies = []
        for row in channel_rows:
            sku = row.get("sku", "")
            channel_available = self.store.to_float(row.get("available_qty"))
            warehouse_available = float(available.get(sku, {}).get("available_to_sell", 0))
            delta = channel_available - warehouse_available
            product = products.get(sku, {})
            unit_cost = self.store.to_float(product.get("unit_cost"))
            if abs(delta) > 1.0 or (warehouse_available and abs(delta) / max(warehouse_available, 1.0) > 0.05):
                discrepancies.append(
                    {
                        "sku": sku,
                        "product_name": product.get("product_name"),
                        "channel": row.get("channel"),
                        "channel_location_id": row.get("location_id"),
                        "channel_available": int(channel_available),
                        "warehouse_available": int(warehouse_available),
                        "delta_units": int(delta),
                        "financial_exposure_inr": round(abs(delta) * unit_cost, 2),
                        "snapshot_at": row.get("snapshot_at"),
                        "issue": "CHANNEL_OVERSTATES" if delta > 0 else "CHANNEL_UNDERSTATES",
                    }
                )

        overlaps = []
        for sku, rows in warehouse_locations.items():
            seen: Dict[str, List[str]] = {}
            for row in rows:
                location_id = row.get("location_id", "")
                group = locations.get(location_id, {}).get("overlap_group", "")
                if group and self.store.to_float(row.get("on_hand_qty")) > 0:
                    seen.setdefault(group, []).append(location_id)
            for row in channel_rows:
                if row.get("sku") != sku:
                    continue
                location_id = row.get("location_id", "")
                group = locations.get(location_id, {}).get("overlap_group", "")
                if group and self.store.to_float(row.get("available_qty")) > 0:
                    seen.setdefault(group, []).append(location_id)
            for group, group_locations in seen.items():
                if len(set(group_locations)) > 1:
                    overlaps.append(
                        {
                            "sku": sku,
                            "overlap_group": group,
                            "locations": sorted(set(group_locations)),
                            "issue": "POSSIBLE_DOUBLE_COUNT",
                        }
                    )

        discrepancies.sort(key=lambda item: item["financial_exposure_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "discrepancies_found": len(discrepancies),
                "overlaps_found": len(overlaps),
                "total_financial_exposure_inr": round(
                    sum(item["financial_exposure_inr"] for item in discrepancies), 2
                ),
            },
            "discrepancies": discrepancies,
            "overlaps": overlaps,
        }

    def _data_quality(self) -> Dict[str, Any]:
        """Detect stale, missing, and incomplete inventory sources."""
        reference = self.store.reference_time()
        locations = [
            row for row in self.store.load("inventory_locations.csv") if row.get("active", "true").lower() == "true"
        ]
        sync_rows = {row.get("location_id", ""): row for row in self.store.load("sync_status.csv")}
        inventory_rows = self.store.inventory_rows()
        products = self.store.products()

        issues = []
        for location in locations:
            location_id = location.get("location_id", "")
            sync = sync_rows.get(location_id)
            if not sync:
                issues.append(
                    {
                        "location_id": location_id,
                        "location_name": location.get("location_name"),
                        "issue": "MISSING_SYNC",
                        "severity": "HIGH",
                    }
                )
                continue
            last_sync = self.store.to_datetime(sync.get("last_sync_at"))
            expected = self.store.to_float(sync.get("expected_interval_minutes"), 60.0)
            if last_sync is None:
                issues.append(
                    {
                        "location_id": location_id,
                        "issue": "UNREADABLE_SYNC_TIMESTAMP",
                        "severity": "HIGH",
                    }
                )
                continue
            age_minutes = (reference - last_sync).total_seconds() / 60.0
            if age_minutes > expected * 2:
                issues.append(
                    {
                        "location_id": location_id,
                        "location_name": location.get("location_name"),
                        "issue": "STALE_SYNC",
                        "age_minutes": int(age_minutes),
                        "expected_interval_minutes": int(expected),
                        "severity": "HIGH" if age_minutes > expected * 4 else "MEDIUM",
                    }
                )

        inventory_skus = {row.get("sku") for row in inventory_rows}
        for sku, product in products.items():
            if product.get("active", "true").lower() == "true" and sku not in inventory_skus:
                issues.append(
                    {
                        "sku": sku,
                        "product_name": product.get("product_name"),
                        "issue": "MISSING_INVENTORY_RECORD",
                        "severity": "HIGH",
                    }
                )

        for row in self.store.load("channel_inventory.csv"):
            if self.store.to_float(row.get("available_qty")) < 0:
                issues.append(
                    {
                        "sku": row.get("sku"),
                        "channel": row.get("channel"),
                        "location_id": row.get("location_id"),
                        "issue": "NEGATIVE_CHANNEL_INVENTORY",
                        "available_qty": int(self.store.to_float(row.get("available_qty"))),
                        "severity": "HIGH",
                    }
                )

        for row in inventory_rows:
            if not row.get("snapshot_at"):
                issues.append(
                    {
                        "sku": row.get("sku"),
                        "location_id": row.get("location_id"),
                        "issue": "MISSING_SNAPSHOT_TIMESTAMP",
                        "severity": "MEDIUM",
                    }
                )

        return {
            "as_of": reference.isoformat(),
            "summary": {
                "issues_found": len(issues),
                "high_severity": sum(1 for issue in issues if issue.get("severity") == "HIGH"),
            },
            "issues": issues,
        }

    def _trace_sku(self, sku: str) -> Dict[str, Any]:
        """Trace ledger events for a SKU and identify how a negative balance arose."""
        rows = [row for row in self.store.load("inventory_ledger.csv") if row.get("sku") == sku]
        rows.sort(key=lambda row: row.get("occurred_at", ""))
        balance = 0.0
        events = []
        negative_events = []
        for row in rows:
            delta = self.store.to_float(row.get("quantity_delta"))
            balance += delta
            event = {
                "event_id": row.get("event_id"),
                "event_type": row.get("event_type"),
                "location_id": row.get("location_id"),
                "quantity_delta": int(delta),
                "balance_after": int(balance),
                "reference_id": row.get("reference_id"),
                "occurred_at": row.get("occurred_at"),
                "note": row.get("note"),
            }
            events.append(event)
            if balance < 0:
                negative_events.append(event)
        channel = [row for row in self.store.load("channel_inventory.csv") if row.get("sku") == sku]
        return {
            "sku": sku,
            "final_ledger_balance": int(balance),
            "negative_balance_detected": balance < 0,
            "first_negative_event": negative_events[0] if negative_events else None,
            "events": events,
            "channel_snapshots": channel,
        }

    def _committed_stock(self) -> Dict[str, Any]:
        """Reconcile reservations and detect over-commitment."""
        grouped = self.store.inventory_by_sku()
        items = []
        for sku, rows in grouped.items():
            on_hand = sum(self.store.to_float(row.get("on_hand_qty")) for row in rows)
            reserved = sum(self.store.to_float(row.get("reserved_qty")) for row in rows)
            unavailable = sum(self.store.to_float(row.get("unavailable_qty")) for row in rows)
            over_committed = reserved > max(0.0, on_hand - unavailable)
            items.append(
                {
                    "sku": sku,
                    "on_hand": int(on_hand),
                    "committed_to_orders": int(reserved),
                    "unavailable": int(unavailable),
                    "free_after_commitments": int(max(0.0, on_hand - unavailable - reserved)),
                    "over_committed": over_committed,
                    "locations": [
                        {
                            "location_id": row.get("location_id"),
                            "reserved": int(self.store.to_float(row.get("reserved_qty"))),
                        }
                        for row in rows
                    ],
                }
            )
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "skus_with_commitments": sum(1 for item in items if item["committed_to_orders"] > 0),
                "over_committed_skus": [item["sku"] for item in items if item["over_committed"]],
            },
            "items": items,
        }

    def _trust_report(self) -> Dict[str, Any]:
        """Score each SKU's inventory evidence and expose unresolved conflicts."""
        discrepancies = self._source_discrepancies()
        data_quality = self._data_quality()
        available = {item["sku"]: item for item in self._available_to_sell()["items"]}
        issues_by_sku: Dict[str, List[Dict[str, Any]]] = {}
        for issue in data_quality["issues"]:
            sku = issue.get("sku")
            if sku:
                issues_by_sku.setdefault(sku, []).append(issue)
        discrepancies_by_sku: Dict[str, List[Dict[str, Any]]] = {}
        for discrepancy in discrepancies["discrepancies"]:
            discrepancies_by_sku.setdefault(discrepancy["sku"], []).append(discrepancy)

        items = []
        for sku, item in available.items():
            score = 100
            unresolved = []
            for issue in issues_by_sku.get(sku, []):
                score -= 25 if issue.get("severity") == "HIGH" else 10
                unresolved.append(issue)
            for discrepancy in discrepancies_by_sku.get(sku, []):
                score -= 30
                unresolved.append(discrepancy)
            if any(
                row.get("sku") == sku and self.store.to_float(row.get("available_qty")) < 0
                for row in self.store.load("channel_inventory.csv")
            ):
                score -= 40
            score = max(0, min(100, score))
            if score >= 85:
                confidence = "HIGH"
            elif score >= 60:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            items.append(
                {
                    "sku": sku,
                    "trusted_quantity": item["available_to_sell"],
                    "confidence": confidence,
                    "trust_score": score,
                    "unresolved_differences": unresolved,
                }
            )
        items.sort(key=lambda item: item["trust_score"])
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "high_confidence": sum(1 for item in items if item["confidence"] == "HIGH"),
                "low_confidence": sum(1 for item in items if item["confidence"] == "LOW"),
            },
            "items": items,
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute an inventory reconciliation action.

        :param args: Dictionary with "action" and optional "sku".
        :param sly_data: Unused context dictionary.
        :return: Structured reconciliation result.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = args.get("action", "summary")
        sku = self.store.normalize_optional(args.get("sku"))
        if action == "available_to_sell":
            return self._available_to_sell(sku)
        if action == "source_discrepancies":
            return self._source_discrepancies()
        if action == "data_quality":
            return self._data_quality()
        if action == "trace_sku":
            if not sku:
                return {"error": "trace_sku requires a sku"}
            return self._trace_sku(sku)
        if action == "committed_stock":
            return self._committed_stock()
        if action == "trust_report":
            return self._trust_report()
        if action == "summary":
            return {
                "available_to_sell": self._available_to_sell(),
                "discrepancies": self._source_discrepancies(),
                "data_quality": self._data_quality(),
                "trust": self._trust_report(),
            }
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
