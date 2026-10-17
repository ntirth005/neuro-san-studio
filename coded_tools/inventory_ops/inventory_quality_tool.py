"""
Inventory quality CodedTool for Neuro-SAN.
Investigates variances, cycle counts, damage, expiry, returns, recurring discrepancies,
quality signals, and recall exposure.
"""

import asyncio
import datetime
import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore

logger = logging.getLogger(__name__)


class InventoryQualityTool(CodedTool):
    """Deterministic quality, expiry, returns, and inventory-integrity engine."""

    def __init__(self) -> None:
        """Initialize with the shared data store."""
        self.store = InventoryDataStore()
        logger.info("%s initialized.", self.__class__.__name__)

    @staticmethod
    def _normalize_action(action: str) -> str:
        """Map natural-language action labels onto supported actions."""
        normalized = str(action).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "variance": "explain_variance",
            "explain_stock_change": "explain_variance",
            "stock_change": "explain_variance",
            "cycle_count": "cycle_count_priority",
            "count_priority": "cycle_count_priority",
            "unsellable": "unsellable_summary",
            "damaged": "unsellable_summary",
            "expiry": "expiry_risk",
            "expiring": "expiry_risk",
            "returns": "return_disposition",
            "return": "return_disposition",
            "recurring": "recurring_discrepancy",
            "repeat_variance": "recurring_discrepancy",
            "quality": "quality_signals",
            "recall": "recall_check",
            "blocked": "recall_check",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "expir" in normalized:
            return "expiry_risk"
        if "recall" in normalized or "block" in normalized:
            return "recall_check"
        if "return" in normalized:
            return "return_disposition"
        if "count" in normalized:
            return "cycle_count_priority"
        if "variance" in normalized or "change" in normalized:
            return "explain_variance"
        if "quality" in normalized:
            return "quality_signals"
        if "recurr" in normalized:
            return "recurring_discrepancy"
        if "unsell" in normalized or "damag" in normalized:
            return "unsellable_summary"
        return normalized

    def _reference_date(self) -> datetime.date:
        """Return the deterministic reference date."""
        return self.store.reference_time().date()

    def _product(self, sku: str) -> Dict[str, str]:
        """Return a product record."""
        return self.store.products().get(sku, {})

    def _batch_rows(
        self,
        sku: Optional[str] = None,
        location_id: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Filter batch records."""
        rows = self.store.inventory_batches()
        if sku:
            rows = [row for row in rows if row.get("sku") == sku]
        if location_id:
            rows = [row for row in rows if row.get("location_id") == location_id]
        if batch_id:
            rows = [row for row in rows if row.get("batch_id") == batch_id]
        return rows

    def _expiry_days(self, row: Dict[str, str]) -> Optional[int]:
        """Return days until expiry, or None when no valid date exists."""
        expiry = row.get("expiry_date", "")
        if not expiry:
            return None
        try:
            return (datetime.date.fromisoformat(expiry) - self._reference_date()).days
        except ValueError:
            return None

    def _batch_state(self, row: Dict[str, str]) -> str:
        """Classify a batch into a usable or unusable state."""
        status = row.get("quality_status", "").lower()
        days = self._expiry_days(row)
        if status == "recalled" or row.get("recall_id"):
            return "recalled"
        if status in {"damaged", "quarantine", "expired", "blocked"}:
            return status
        if days is not None and days < 0:
            return "expired"
        return "sellable"

    def _velocity(self, sku: str, location_id: str, window: int = 28) -> float:
        """Return average daily demand for a SKU and location."""
        rows = [
            row
            for row in self.store.location_demand()
            if row.get("sku") == sku and row.get("location_id") == location_id
        ]
        if not rows:
            return 0.0
        dates = sorted(row.get("date", "") for row in rows)
        start = (datetime.date.fromisoformat(dates[-1]) - datetime.timedelta(days=window - 1)).isoformat()
        values = [self.store.to_float(row.get("units")) for row in rows if row.get("date", "") >= start]
        return sum(values) / len(values) if values else 0.0

    def _explain_variance(self, sku: Optional[str], location_id: Optional[str]) -> Dict[str, Any]:
        """Explain unexpected stock changes from ledger, counts, and batch evidence."""
        ledger = self.store.load("inventory_ledger.csv")
        if sku:
            ledger = [row for row in ledger if row.get("sku") == sku]
        if location_id:
            ledger = [row for row in ledger if row.get("location_id") == location_id]
        events = []
        by_type: Dict[str, Dict[str, float]] = {}
        for row in sorted(ledger, key=lambda item: item.get("occurred_at", "")):
            delta = self.store.to_float(row.get("quantity_delta"))
            by_type.setdefault(row.get("event_type", "unknown"), {"events": 0, "units": 0.0})
            by_type[row.get("event_type", "unknown")]["events"] += 1
            by_type[row.get("event_type", "unknown")]["units"] += delta
            events.append(
                {
                    "event_id": row.get("event_id"),
                    "event_type": row.get("event_type"),
                    "location_id": row.get("location_id"),
                    "quantity_delta": int(delta),
                    "reference_id": row.get("reference_id"),
                    "occurred_at": row.get("occurred_at"),
                    "note": row.get("note"),
                }
            )
        counts = [
            row
            for row in self.store.inventory_counts()
            if (not sku or row.get("sku") == sku) and (not location_id or row.get("location_id") == location_id)
        ]
        count_variances = [
            {
                "count_id": row.get("count_id"),
                "counted_at": row.get("counted_at"),
                "system_qty": self.store.to_int(row.get("system_qty")),
                "counted_qty": self.store.to_int(row.get("counted_qty")),
                "variance": self.store.to_int(row.get("variance")),
            }
            for row in counts
        ]
        recorded_on_hand = 0.0
        batch_total = 0.0
        for row in self.store.inventory_rows():
            if sku and row.get("sku") != sku:
                continue
            if location_id and row.get("location_id") != location_id:
                continue
            recorded_on_hand += self.store.to_float(row.get("on_hand_qty"))
        for row in self._batch_rows(sku, location_id):
            batch_total += self.store.to_float(row.get("quantity"))
        ledger_balance = sum(self.store.to_float(row.get("quantity_delta")) for row in ledger)
        unexplained = recorded_on_hand - ledger_balance
        unusual = [
            event
            for event in events
            if event["event_type"] in {"adjustment", "sync_adjustment"} or event["quantity_delta"] < 0
        ]
        return {
            "as_of": self.store.reference_time().isoformat(),
            "sku": sku,
            "location_id": location_id,
            "summary": {
                "recorded_on_hand": int(recorded_on_hand),
                "ledger_balance": int(ledger_balance),
                "unexplained_difference_units": int(unexplained),
                "count_variances": len([item for item in count_variances if item["variance"] != 0]),
                "batch_total": int(batch_total),
            },
            "event_breakdown": [
                {"event_type": event_type, "events": int(values["events"]), "units": int(values["units"])}
                for event_type, values in sorted(by_type.items())
            ],
            "unusual_events": unusual,
            "count_variances": count_variances,
            "root_cause_candidates": [
                "Manual adjustment or cycle-count correction.",
                "Marketplace sync did not reverse an oversell.",
                "Opening balance is missing from the ledger.",
                "Batch-level damage or expiry is not reflected in the on-hand record.",
            ]
            if unexplained or unusual
            else ["No material unexplained movement detected."],
            "confidence": "MEDIUM" if unexplained else "HIGH",
        }

    def _cycle_count_priority(self, limit: int) -> Dict[str, Any]:
        """Rank SKUs and locations for physical cycle counting."""
        counts_by_key: Dict[tuple, Dict[str, str]] = {}
        for row in self.store.inventory_counts():
            key = (row.get("sku", ""), row.get("location_id", ""))
            if key not in counts_by_key or row.get("counted_at", "") > counts_by_key[key].get("counted_at", ""):
                counts_by_key[key] = row
        items = []
        for row in self.store.inventory_rows():
            sku = row.get("sku", "")
            location_id = row.get("location_id", "")
            product = self._product(sku)
            on_hand = self.store.to_float(row.get("on_hand_qty"))
            unit_cost = self.store.to_float(product.get("unit_cost"))
            value = on_hand * unit_cost
            velocity = self._velocity(sku, location_id)
            movement_value = velocity * unit_cost
            last_count = counts_by_key.get((sku, location_id), {})
            counted_at = last_count.get("counted_at", "")
            days_since = (self._reference_date() - datetime.date.fromisoformat(counted_at)).days if counted_at else 999
            variance = abs(self.store.to_int(last_count.get("variance")))
            batch_total = sum(
                self.store.to_float(batch.get("quantity")) for batch in self._batch_rows(sku, location_id)
            )
            batch_gap = abs(on_hand - batch_total)
            score = (
                value / 1000.0
                + movement_value / 100.0
                + variance * 10.0
                + batch_gap * 5.0
                + min(days_since, 120) / 12.0
            )
            items.append(
                {
                    "sku": sku,
                    "location_id": location_id,
                    "on_hand_qty": int(on_hand),
                    "inventory_value_inr": round(value, 2),
                    "daily_movement_value_inr": round(movement_value, 2),
                    "last_counted_at": counted_at or None,
                    "days_since_last_count": days_since if days_since < 999 else None,
                    "last_variance_units": variance,
                    "batch_gap_units": int(batch_gap),
                    "priority_score": round(score, 2),
                    "reason": (
                        "High-value item with a material variance."
                        if variance and value > 1000
                        else "High movement value and stale count."
                        if movement_value > 100 and days_since > 30
                        else "Batch quantity does not reconcile to on-hand."
                        if batch_gap
                        else "Routine materiality-based count."
                    ),
                }
            )
        items.sort(key=lambda item: item["priority_score"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "items_ranked": len(items),
                "top_priority": items[0] if items else None,
            },
            "items": items[:limit],
        }

    def _unsellable_summary(self) -> Dict[str, Any]:
        """Separate usable and unusable inventory while preserving evidence."""
        states = ["sellable", "damaged", "quarantine", "expired", "recalled", "blocked"]
        totals = {state: {"units": 0.0, "value_inr": 0.0} for state in states}
        items = []
        for row in self.store.inventory_batches():
            state = self._batch_state(row)
            quantity = self.store.to_float(row.get("quantity"))
            unit_cost = self.store.to_float(self._product(row.get("sku", "")).get("unit_cost"))
            totals.setdefault(state, {"units": 0.0, "value_inr": 0.0})
            totals[state]["units"] += quantity
            totals[state]["value_inr"] += quantity * unit_cost
            items.append(
                {
                    "batch_id": row.get("batch_id"),
                    "sku": row.get("sku"),
                    "location_id": row.get("location_id"),
                    "quantity": int(quantity),
                    "state": state,
                    "expiry_date": row.get("expiry_date"),
                    "recall_id": row.get("recall_id"),
                    "value_at_cost_inr": round(quantity * unit_cost, 2),
                }
            )
        unusable_states = {"damaged", "quarantine", "expired", "recalled", "blocked"}
        unusable = [item for item in items if item["state"] in unusable_states]
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "total_units": int(sum(value["units"] for value in totals.values())),
                "usable_units": int(totals["sellable"]["units"]),
                "unusable_units": int(sum(totals[state]["units"] for state in unusable_states)),
                "unusable_value_inr": round(sum(totals[state]["value_inr"] for state in unusable_states), 2),
                "by_state": {
                    state: {"units": int(value["units"]), "value_inr": round(value["value_inr"], 2)}
                    for state, value in totals.items()
                },
            },
            "unusable_batches": sorted(unusable, key=lambda item: item["value_at_cost_inr"], reverse=True),
            "audit_note": "Adjustment and quality-event history is retained; unusable units are not sellable.",
        }

    def _expiry_risk(self, horizon_days: int) -> Dict[str, Any]:
        """Prioritize batches by expiry date and remaining demand."""
        items = []
        for row in self.store.inventory_batches():
            days = self._expiry_days(row)
            if days is None or days > horizon_days:
                continue
            sku = row.get("sku", "")
            location_id = row.get("location_id", "")
            quantity = self.store.to_float(row.get("quantity"))
            velocity = self._velocity(sku, location_id)
            demand_before_expiry = max(0.0, velocity * max(days, 0))
            projected_waste = max(0.0, quantity - demand_before_expiry)
            unit_cost = self.store.to_float(self._product(sku).get("unit_cost"))
            items.append(
                {
                    "batch_id": row.get("batch_id"),
                    "sku": sku,
                    "location_id": location_id,
                    "quantity": int(quantity),
                    "expiry_date": row.get("expiry_date"),
                    "days_to_expiry": days,
                    "daily_demand": round(velocity, 2),
                    "demand_before_expiry_units": round(demand_before_expiry, 1),
                    "projected_waste_units": int(projected_waste),
                    "projected_waste_value_inr": round(projected_waste * unit_cost, 2),
                    "priority": "EXPIRED" if days < 0 else "HIGH" if days <= 14 else "MEDIUM",
                }
            )
        items.sort(key=lambda item: (item["days_to_expiry"], -item["projected_waste_value_inr"]))
        return {
            "as_of": self.store.reference_time().isoformat(),
            "horizon_days": horizon_days,
            "summary": {
                "batches_nearing_expiry": len(items),
                "expired_batches": sum(1 for item in items if item["days_to_expiry"] < 0),
                "projected_waste_units": sum(item["projected_waste_units"] for item in items),
                "projected_waste_value_inr": round(sum(item["projected_waste_value_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _return_disposition(self, return_id: Optional[str], sku: Optional[str]) -> Dict[str, Any]:
        """Route returns to restock, inspection, quarantine, or supplier claim."""
        rows = self.store.load("returns.csv")
        if return_id:
            rows = [row for row in rows if row.get("return_id") == return_id]
        if sku:
            rows = [row for row in rows if row.get("sku") == sku]
        items = []
        for row in rows:
            condition = row.get("condition", "").lower()
            reason = row.get("return_reason", "")
            quantity = self.store.to_int(row.get("quantity"))
            unit_cost = self.store.to_float(self._product(row.get("sku", "")).get("unit_cost"))
            supplier_claim = reason in {"defective", "not_as_described"} and condition != "resellable"
            if condition == "resellable":
                disposition = "restock"
                inspection = False
                eligible = True
            elif condition == "damaged":
                disposition = "quarantine"
                inspection = True
                eligible = False
            elif condition == "in_transit":
                disposition = "inspect_on_arrival"
                inspection = True
                eligible = False
            else:
                disposition = "manual_review"
                inspection = True
                eligible = False
            items.append(
                {
                    "return_id": row.get("return_id"),
                    "sku": row.get("sku"),
                    "quantity": quantity,
                    "condition": condition,
                    "return_reason": reason,
                    "refund_status": row.get("refund_status"),
                    "disposition": disposition,
                    "eligible_for_sellable_inventory": eligible,
                    "requires_inspection": inspection,
                    "supplier_claim_required": supplier_claim,
                    "value_at_cost_inr": round(quantity * unit_cost, 2),
                }
            )
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "returns_evaluated": len(items),
                "restock_eligible_units": sum(
                    item["quantity"] for item in items if item["eligible_for_sellable_inventory"]
                ),
                "inspection_or_quarantine_units": sum(
                    item["quantity"] for item in items if not item["eligible_for_sellable_inventory"]
                ),
            },
            "items": items,
        }

    def _recurring_discrepancy(self) -> Dict[str, Any]:
        """Detect repeated variance patterns by SKU and location."""
        counts: Dict[tuple, List[Dict[str, str]]] = {}
        for row in self.store.inventory_counts():
            counts.setdefault((row.get("sku", ""), row.get("location_id", "")), []).append(row)
        ledger: Dict[tuple, List[Dict[str, str]]] = {}
        for row in self.store.load("inventory_ledger.csv"):
            if row.get("event_type") in {"adjustment", "sync_adjustment"}:
                ledger.setdefault((row.get("sku", ""), row.get("location_id", "")), []).append(row)
        patterns = []
        for key, rows in counts.items():
            non_zero = [row for row in rows if self.store.to_int(row.get("variance")) != 0]
            if len(non_zero) < 2 and not ledger.get(key):
                continue
            total_variance = sum(self.store.to_int(row.get("variance")) for row in rows)
            sku, location_id = key
            patterns.append(
                {
                    "sku": sku,
                    "location_id": location_id,
                    "counts_recorded": len(rows),
                    "counts_with_variance": len(non_zero),
                    "total_variance_units": total_variance,
                    "ledger_adjustments": len(ledger.get(key, [])),
                    "pattern": (
                        "RECURRING_NEGATIVE_VARIANCE"
                        if len(non_zero) >= 2 and total_variance < 0
                        else "RECURRING_ADJUSTMENT"
                        if len(ledger.get(key, [])) >= 2
                        else "ISOLATED_VARIANCE"
                    ),
                    "root_cause_candidates": [
                        "Receiving or put-away quantity is consistently overstated.",
                        "Marketplace sync is not reversing oversells.",
                        "Pick errors or unrecorded damage are reducing physical stock.",
                    ],
                }
            )
        patterns.sort(key=lambda item: (item["counts_with_variance"], abs(item["total_variance_units"])), reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "patterns_found": len(patterns),
                "recurring_patterns": sum(1 for item in patterns if item["pattern"].startswith("RECURRING")),
            },
            "patterns": patterns,
        }

    def _quality_signals(self) -> Dict[str, Any]:
        """Group returns and quality events into suspected quality issues."""
        returns = self.store.load("returns.csv")
        events = self.store.quality_events()
        suppliers = {row.get("sku", ""): row for row in self.store.load("suppliers.csv")}
        groups: Dict[str, Dict[str, Any]] = {}
        for row in returns:
            sku = row.get("sku", "")
            group = groups.setdefault(
                sku,
                {
                    "sku": sku,
                    "return_units": 0,
                    "damaged_units": 0,
                    "defective_units": 0,
                    "quality_events": 0,
                    "event_types": set(),
                    "supplier_id": suppliers.get(sku, {}).get("supplier_id"),
                    "supplier_quality_reject_pct": self.store.to_float(
                        suppliers.get(sku, {}).get("quality_reject_pct")
                    ),
                },
            )
            quantity = self.store.to_int(row.get("quantity"))
            group["return_units"] += quantity
            if row.get("condition") == "damaged":
                group["damaged_units"] += quantity
            if row.get("return_reason") in {"defective", "not_as_described"}:
                group["defective_units"] += quantity
        for row in events:
            sku = row.get("sku", "")
            group = groups.setdefault(
                sku,
                {
                    "sku": sku,
                    "return_units": 0,
                    "damaged_units": 0,
                    "defective_units": 0,
                    "quality_events": 0,
                    "event_types": set(),
                    "supplier_id": row.get("supplier_id"),
                    "supplier_quality_reject_pct": 0.0,
                },
            )
            group["quality_events"] += 1
            group["event_types"].add(row.get("event_type"))
        items = []
        for group in groups.values():
            signals = (
                (1 if group["damaged_units"] else 0)
                + (1 if group["defective_units"] else 0)
                + (1 if group["quality_events"] else 0)
                + (1 if group["supplier_quality_reject_pct"] >= 2.0 else 0)
            )
            items.append(
                {
                    "sku": group["sku"],
                    "supplier_id": group["supplier_id"],
                    "return_units": group["return_units"],
                    "damaged_units": group["damaged_units"],
                    "defective_units": group["defective_units"],
                    "quality_events": group["quality_events"],
                    "event_types": sorted(group["event_types"]),
                    "supplier_quality_reject_pct": group["supplier_quality_reject_pct"],
                    "signal_count": signals,
                    "suspected_quality_issue": signals >= 2,
                    "recommended_action": (
                        "Escalate to supplier quality review and quarantine affected batches."
                        if signals >= 2
                        else "Monitor; no multi-signal quality pattern yet."
                    ),
                }
            )
        items.sort(key=lambda item: item["signal_count"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "skus_with_signals": sum(1 for item in items if item["signal_count"] > 0),
                "suspected_quality_issues": sum(1 for item in items if item["suspected_quality_issue"]),
            },
            "items": items,
        }

    def _recall_check(self, sku: Optional[str], channel: Optional[str]) -> Dict[str, Any]:
        """Check whether recalled or blocked stock is still exposed for sale."""
        recalls = self.store.recalls()
        if sku:
            recalls = [row for row in recalls if row.get("sku") == sku]
        active = [row for row in recalls if row.get("status", "").lower() == "active"]
        items = []
        for recall in active:
            recall_sku = recall.get("sku", "")
            batches = [row for row in self._batch_rows(recall_sku, batch_id=recall.get("batch_id"))]
            recalled_units = sum(self.store.to_float(row.get("quantity")) for row in batches)
            channel_rows = [
                row
                for row in self.store.load("channel_inventory.csv")
                if row.get("sku") == recall_sku and (not channel or row.get("channel") == channel)
            ]
            channel_units = sum(self.store.to_float(row.get("available_qty")) for row in channel_rows)
            items.append(
                {
                    "recall_id": recall.get("recall_id"),
                    "sku": recall_sku,
                    "batch_id": recall.get("batch_id"),
                    "reason": recall.get("reason"),
                    "recalled_units": int(recalled_units),
                    "channel_available_units": int(channel_units),
                    "channel_records": channel_rows,
                    "still_exposed": channel_units > 0,
                    "exposure_risk": "HIGH" if channel_units > 0 else "LOW",
                    "recommended_action": (
                        "Block the SKU/channel listing, quarantine the batch, and notify marketplace operations."
                        if channel_units > 0
                        else "Quarantine the batch and confirm no channel exposure."
                    ),
                }
            )
        blocked_batches = [
            row
            for row in self.store.inventory_batches()
            if self._batch_state(row) in {"recalled", "blocked", "quarantine"} and (not sku or row.get("sku") == sku)
        ]
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "active_recalls": len(active),
                "recalled_skus": sorted({row.get("sku") for row in active}),
                "recalled_units": int(sum(item["recalled_units"] for item in items)),
                "channel_exposure_units": int(sum(item["channel_available_units"] for item in items)),
                "selling_recalled_stock": any(item["still_exposed"] for item in items),
            },
            "recalls": items,
            "blocked_batches": blocked_batches,
            "guardrail": "Positive physical stock is not sellable while a recall or quality block is active.",
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Execute an inventory quality action."""
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = self._normalize_action(args.get("action", "unsellable_summary"))
        sku = self.store.normalize_optional(args.get("sku"))
        location_id = self.store.normalize_optional(args.get("location_id"))
        channel = self.store.normalize_optional(args.get("channel"))
        return_id = self.store.normalize_optional(args.get("return_id"))
        limit = self.store.to_int(args.get("limit"), 10)
        horizon = self.store.to_int(args.get("horizon_days"), 45)

        if action == "explain_variance":
            return self._explain_variance(sku, location_id)
        if action == "cycle_count_priority":
            return self._cycle_count_priority(limit)
        if action == "unsellable_summary":
            return self._unsellable_summary()
        if action == "expiry_risk":
            return self._expiry_risk(horizon)
        if action == "return_disposition":
            return self._return_disposition(return_id, sku)
        if action == "recurring_discrepancy":
            return self._recurring_discrepancy()
        if action == "quality_signals":
            return self._quality_signals()
        if action == "recall_check":
            return self._recall_check(sku, channel)
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
