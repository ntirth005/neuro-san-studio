"""
Multi-location fulfillment CodedTool for Neuro-SAN.
Answers transfer, channel sellability, allocation, redistribution, and closure questions.
"""

import asyncio
import datetime
import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore

logger = logging.getLogger(__name__)


class NetworkFulfillmentTool(CodedTool):
    """Deterministic multi-location inventory and order-fulfillment engine."""

    def __init__(self) -> None:
        """Initialize with the shared data store."""
        self.store = InventoryDataStore()
        logger.info("%s initialized.", self.__class__.__name__)

    @staticmethod
    def _normalize_action(action: str) -> str:
        """Map natural-language action labels onto supported actions."""
        normalized = str(action).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "fulfill": "fulfill_options",
            "fulfillment_options": "fulfill_options",
            "transfer_options": "fulfill_options",
            "stockout_risk": "location_stockout_risk",
            "location_risk": "location_stockout_risk",
            "transfer_candidate": "transfer_candidates",
            "excess_stock": "transfer_candidates",
            "in_transit": "in_transit",
            "transfers_in_transit": "in_transit",
            "sellability": "sellability_check",
            "channel_availability": "sellability_check",
            "allocation_conflict": "allocation_conflicts",
            "order_conflicts": "allocation_conflicts",
            "redistribute": "redistribute_plan",
            "distribution_plan": "redistribute_plan",
            "closure": "closure_simulation",
            "location_closure": "closure_simulation",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "transit" in normalized:
            return "in_transit"
        if "closure" in normalized or "close" in normalized:
            return "closure_simulation"
        if "allocation" in normalized or "conflict" in normalized:
            return "allocation_conflicts"
        if "sellab" in normalized or "channel" in normalized:
            return "sellability_check"
        if "redistribut" in normalized or "distribution" in normalized:
            return "redistribute_plan"
        if "transfer" in normalized and "candidate" in normalized:
            return "transfer_candidates"
        if "stockout" in normalized or "risk" in normalized:
            return "location_stockout_risk"
        if "fulfil" in normalized or "order" in normalized:
            return "fulfill_options"
        return normalized

    def _product(self, sku: str) -> Dict[str, str]:
        """Return a product record."""
        return self.store.products().get(sku, {})

    def _location_meta(self) -> Dict[str, Dict[str, str]]:
        """Return location metadata keyed by location id."""
        return {row.get("location_id", ""): row for row in self.store.load("inventory_locations.csv")}

    def _positions(self) -> Dict[Tuple[str, str], Dict[str, float]]:
        """Aggregate physical inventory by SKU and location."""
        positions: Dict[Tuple[str, str], Dict[str, float]] = {}
        for row in self.store.inventory_rows():
            key = (row.get("sku", ""), row.get("location_id", ""))
            entry = positions.setdefault(
                key,
                {"on_hand": 0.0, "reserved": 0.0, "incoming": 0.0, "unavailable": 0.0},
            )
            entry["on_hand"] += self.store.to_float(row.get("on_hand_qty"))
            entry["reserved"] += self.store.to_float(row.get("reserved_qty"))
            entry["incoming"] += self.store.to_float(row.get("incoming_qty"))
            entry["unavailable"] += self.store.to_float(row.get("unavailable_qty"))
        for entry in positions.values():
            entry["free"] = max(0.0, entry["on_hand"] - entry["reserved"] - entry["unavailable"])
        return positions

    def _batch_usable(self, sku: str, location_id: str) -> Optional[float]:
        """Return sellable, unexpired, non-recalled batch quantity for a location."""
        reference_date = self.store.reference_time().date()
        rows = [
            row
            for row in self.store.inventory_batches()
            if row.get("sku") == sku and row.get("location_id") == location_id
        ]
        if not rows:
            return None
        usable = 0.0
        for row in rows:
            if row.get("quality_status", "").lower() != "sellable":
                continue
            if row.get("recall_id"):
                continue
            expiry = row.get("expiry_date", "")
            if expiry:
                try:
                    if datetime.date.fromisoformat(expiry) < reference_date:
                        continue
                except ValueError:
                    pass
            usable += self.store.to_float(row.get("quantity"))
        return usable

    def _usable(self, sku: str, location_id: str) -> float:
        """Return usable quantity, falling back to free stock when batches are absent."""
        position = self._positions().get((sku, location_id), {})
        free = float(position.get("free", 0.0))
        batch_usable = self._batch_usable(sku, location_id)
        if batch_usable is None:
            return free
        return min(free, batch_usable)

    def _demand_velocity(self, sku: str, location_id: str, window: int = 28) -> Tuple[float, float]:
        """Return average daily demand and standard deviation for a location."""
        rows = [
            row
            for row in self.store.location_demand()
            if row.get("sku") == sku and row.get("location_id") == location_id
        ]
        if not rows:
            return 0.0, 0.0
        dates = sorted(row.get("date", "") for row in rows)
        latest = datetime.date.fromisoformat(dates[-1])
        start = (latest - datetime.timedelta(days=window - 1)).isoformat()
        values = [self.store.to_float(row.get("units")) for row in rows if row.get("date", "") >= start]
        if not values:
            return 0.0, 0.0
        mean = sum(values) / len(values)
        if len(values) < 2:
            return mean, mean * 0.5
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        return mean, variance**0.5

    def _lane(self, from_location: str, to_location: str) -> Optional[Dict[str, str]]:
        """Return the transfer lane between two locations."""
        if from_location == to_location:
            return {"lead_time_days": "0", "cost_per_unit_inr": "0", "max_transfer_qty": "9999"}
        for row in self.store.transfer_lanes():
            if row.get("from_location_id") == from_location and row.get("to_location_id") == to_location:
                return row
        return None

    def _channel_available(self, sku: str, channel: str) -> List[Dict[str, Any]]:
        """Return channel-level available inventory records for a SKU."""
        return [
            {
                "channel": row.get("channel"),
                "location_id": row.get("location_id"),
                "available_qty": int(self.store.to_float(row.get("available_qty"))),
                "committed_qty": int(self.store.to_float(row.get("committed_qty"))),
                "snapshot_at": row.get("snapshot_at"),
            }
            for row in self.store.load("channel_inventory.csv")
            if row.get("sku") == sku and (not channel or row.get("channel") == channel)
        ]

    def _sellability_rule(self, channel: str, location_id: str) -> Optional[Dict[str, str]]:
        """Return the most specific sellability rule for a channel and location."""
        location = self._location_meta().get(location_id, {})
        location_type = location.get("location_type", "")
        rules = self.store.sellability_rules()
        exact_channel_rules = [row for row in rules if row.get("channel") == channel]
        candidate_rules = exact_channel_rules or [row for row in rules if row.get("channel") == "all"]
        matches = []
        for row in candidate_rules:
            row_location = row.get("location_id", "")
            row_type = row.get("location_type", "")
            if row_location and row_location != location_id:
                continue
            if not row_location and row_type and row_type != location_type:
                continue
            specificity = (2 if row_location else 0) + (1 if row_type else 0)
            matches.append((specificity, row))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        return matches[0][1]

    def _order(self, order_id: Optional[str]) -> Optional[Dict[str, str]]:
        """Return a single order line."""
        if not order_id:
            return None
        for row in self.store.customer_orders():
            if row.get("order_id") == order_id:
                return row
        return None

    def _fulfill_options(
        self,
        sku: str,
        quantity: int,
        destination_location_id: Optional[str],
        order_id: Optional[str],
    ) -> Dict[str, Any]:
        """Compare transfer fulfillment against buying more stock."""
        order = self._order(order_id)
        if order:
            sku = sku or order.get("sku", "")
            quantity = quantity or self.store.to_int(order.get("quantity"))
            destination_location_id = destination_location_id or order.get("requested_location_id")
        if not sku or quantity <= 0:
            return {"error": "fulfill_options requires a sku and a positive quantity"}
        product = self._product(sku)
        if not product:
            return {"error": f"SKU {sku} not found in product catalog"}
        supplier = self.store.suppliers().get(sku, {})
        supplier_lead_time = self.store.to_float(supplier.get("lead_time_days"), 7.0)
        unit_cost = self.store.to_float(product.get("unit_cost"))
        candidates = []
        for (position_sku, location_id), position in self._positions().items():
            if position_sku != sku:
                continue
            usable = self._usable(sku, location_id)
            if usable <= 0:
                continue
            if destination_location_id and location_id == destination_location_id:
                lane = self._lane(location_id, location_id)
            elif destination_location_id:
                lane = self._lane(location_id, destination_location_id)
            else:
                lane = {"lead_time_days": "0", "cost_per_unit_inr": "0", "max_transfer_qty": "9999"}
            if lane is None:
                continue
            transferable = min(usable, float(quantity), self.store.to_float(lane.get("max_transfer_qty")))
            transfer_cost = transferable * self.store.to_float(lane.get("cost_per_unit_inr"))
            candidates.append(
                {
                    "from_location_id": location_id,
                    "usable_units": int(usable),
                    "transferable_units": int(transferable),
                    "transfer_lead_time_days": int(self.store.to_float(lane.get("lead_time_days"))),
                    "transfer_cost_inr": round(transfer_cost, 2),
                    "transfer_cost_per_unit_inr": self.store.to_float(lane.get("cost_per_unit_inr")),
                }
            )
        candidates.sort(key=lambda item: (item["transfer_lead_time_days"], item["transfer_cost_inr"]))
        remaining = quantity
        plan = []
        for candidate in candidates:
            if remaining <= 0:
                break
            take = min(remaining, candidate["transferable_units"])
            if take <= 0:
                continue
            allocated_cost = round(take * candidate["transfer_cost_per_unit_inr"], 2)
            plan.append(
                {
                    **candidate,
                    "allocated_units": int(take),
                    "allocated_transfer_cost_inr": allocated_cost,
                }
            )
            remaining -= take
        transfer_cost_total = round(sum(item["allocated_transfer_cost_inr"] for item in plan), 2)
        buy_cost = round(quantity * unit_cost, 2)
        feasible = remaining <= 0
        expected_days = max((item["transfer_lead_time_days"] for item in plan), default=0)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "sku": sku,
            "product_name": product.get("product_name"),
            "quantity_requested": quantity,
            "destination_location_id": destination_location_id,
            "order_id": order_id,
            "feasible_from_network": feasible,
            "unfulfilled_units": int(max(0, remaining)),
            "fulfillment_plan": plan,
            "total_transfer_cost_inr": transfer_cost_total,
            "expected_delivery_days": expected_days,
            "buy_alternative": {
                "unit_cost_inr": unit_cost,
                "purchase_cost_inr": buy_cost,
                "supplier_lead_time_days": int(supplier_lead_time),
                "supplier_id": supplier.get("supplier_id"),
            },
            "recommendation": (
                "Fulfil from the network; the transfer is faster and cheaper than a new purchase."
                if feasible and expected_days <= supplier_lead_time
                else "Use available transfers and purchase the remaining units."
                if not feasible
                else "Compare transfer cost against the supplier purchase before committing."
            ),
        }

    def _location_stockout_risk(self, horizon_days: int) -> Dict[str, Any]:
        """Rank locations by projected stockout date."""
        reference = self.store.reference_time().date()
        items = []
        for (sku, location_id), position in self._positions().items():
            velocity, std_dev = self._demand_velocity(sku, location_id)
            usable = self._usable(sku, location_id)
            incoming = float(position.get("incoming", 0.0))
            days_of_supply = round(usable / velocity, 1) if velocity > 0 else 999.0
            stockout_date = (
                (reference + datetime.timedelta(days=max(0, int(days_of_supply)))).isoformat()
                if velocity > 0
                else None
            )
            if velocity <= 0:
                risk = "NO_DEMAND"
            elif days_of_supply < 7:
                risk = "CRITICAL"
            elif days_of_supply < 14:
                risk = "WARNING"
            else:
                risk = "HEALTHY"
            items.append(
                {
                    "sku": sku,
                    "location_id": location_id,
                    "usable_units": int(usable),
                    "incoming_units": int(incoming),
                    "daily_demand": round(velocity, 2),
                    "demand_std_dev": round(std_dev, 2),
                    "days_of_supply": days_of_supply,
                    "projected_stockout_date": stockout_date,
                    "risk": risk,
                    "within_horizon": days_of_supply <= horizon_days if velocity > 0 else False,
                }
            )
        items.sort(key=lambda item: item["days_of_supply"])
        return {
            "as_of": self.store.reference_time().isoformat(),
            "horizon_days": horizon_days,
            "summary": {
                "locations_evaluated": len(items),
                "critical": sum(1 for item in items if item["risk"] == "CRITICAL"),
                "warning": sum(1 for item in items if item["risk"] == "WARNING"),
                "first_to_run_out": items[0] if items else None,
            },
            "items": items,
        }

    def _transfer_candidates(self, sku: Optional[str], target_location_id: Optional[str]) -> Dict[str, Any]:
        """Identify excess locations that can cover a shortage elsewhere."""
        products = self.store.products()
        positions = self._positions()
        candidates = []
        for product_sku in products:
            if sku and product_sku != sku:
                continue
            for (position_sku, source_location), position in positions.items():
                if position_sku != product_sku:
                    continue
                source_velocity, _ = self._demand_velocity(product_sku, source_location)
                source_usable = self._usable(product_sku, source_location)
                source_dos = source_usable / source_velocity if source_velocity > 0 else 999.0
                excess = max(0.0, source_usable - source_velocity * 21.0)
                if excess <= 0:
                    continue
                for (target_sku, target_location), target_position in positions.items():
                    if target_sku != product_sku or target_location == source_location:
                        continue
                    if target_location_id and target_location != target_location_id:
                        continue
                    target_velocity, _ = self._demand_velocity(product_sku, target_location)
                    if target_velocity <= 0:
                        continue
                    target_usable = self._usable(product_sku, target_location)
                    target_lead_time = self.store.to_float(
                        self.store.suppliers().get(product_sku, {}).get("lead_time_days"), 7.0
                    )
                    target_dos = target_usable / target_velocity
                    shortage = max(0.0, target_velocity * (target_lead_time + 2.0) - target_usable)
                    if shortage <= 0:
                        continue
                    lane = self._lane(source_location, target_location)
                    if not lane:
                        continue
                    transfer_qty = min(excess, shortage, self.store.to_float(lane.get("max_transfer_qty")))
                    if transfer_qty < 1:
                        continue
                    transfer_qty = int(transfer_qty)
                    cost = transfer_qty * self.store.to_float(lane.get("cost_per_unit_inr"))
                    candidates.append(
                        {
                            "sku": product_sku,
                            "product_name": products[product_sku].get("product_name"),
                            "from_location_id": source_location,
                            "to_location_id": target_location,
                            "source_usable_units": int(source_usable),
                            "source_days_of_supply": round(source_dos, 1),
                            "target_usable_units": int(target_usable),
                            "target_days_of_supply": round(target_dos, 1),
                            "transfer_qty": transfer_qty,
                            "transfer_lead_time_days": int(self.store.to_float(lane.get("lead_time_days"))),
                            "transfer_cost_inr": round(cost, 2),
                            "target_dos_after_transfer": round((target_usable + transfer_qty) / target_velocity, 1),
                        }
                    )
        candidates.sort(key=lambda item: item["target_days_of_supply"])
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "transfer_candidates": len(candidates),
                "total_units": sum(item["transfer_qty"] for item in candidates),
                "total_transfer_cost_inr": round(sum(item["transfer_cost_inr"] for item in candidates), 2),
            },
            "candidates": candidates,
        }

    def _in_transit(self, sku: Optional[str]) -> Dict[str, Any]:
        """Reconcile transfer records and shipment timing."""
        reference = self.store.reference_time().date()
        rows = [row for row in self.store.transfers() if not sku or row.get("sku") == sku]
        items = []
        for row in rows:
            expected = row.get("expected_arrival", "")
            expected_date = None
            if expected:
                try:
                    expected_date = datetime.date.fromisoformat(expected)
                except ValueError:
                    expected_date = None
            status = row.get("status", "").lower()
            delayed = bool(expected_date and expected_date < reference and status not in {"received", "cancelled"})
            days_until = (expected_date - reference).days if expected_date else None
            items.append(
                {
                    "transfer_id": row.get("transfer_id"),
                    "sku": row.get("sku"),
                    "from_location_id": row.get("from_location_id"),
                    "to_location_id": row.get("to_location_id"),
                    "quantity": self.store.to_int(row.get("quantity")),
                    "status": status,
                    "expected_arrival": expected,
                    "days_until_arrival": days_until,
                    "delayed": delayed,
                    "transfer_cost_inr": self.store.to_float(row.get("transfer_cost_inr")),
                    "linked_order_id": row.get("linked_order_id"),
                }
            )
        in_transit = [item for item in items if item["status"] in {"in_transit", "delayed", "planned"}]
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "records": len(items),
                "in_transit_or_planned_units": sum(item["quantity"] for item in in_transit),
                "delayed_transfers": sum(1 for item in items if item["delayed"]),
                "arriving_within_2_days": sum(
                    1
                    for item in in_transit
                    if item["days_until_arrival"] is not None and 0 <= item["days_until_arrival"] <= 2
                ),
            },
            "items": sorted(items, key=lambda item: item["expected_arrival"] or "9999-12-31"),
        }

    def _sellability_check(self, sku: str, channel: Optional[str]) -> Dict[str, Any]:
        """Explain why physical stock may not be sellable online."""
        product = self._product(sku)
        if not product:
            return {"error": f"SKU {sku} not found in product catalog"}
        locations = []
        for (position_sku, location_id), position in self._positions().items():
            if position_sku != sku:
                continue
            physical = self.store.to_float(position.get("on_hand"))
            free = float(position.get("free", 0.0))
            usable = self._usable(sku, location_id)
            expired_or_blocked = max(0.0, free - usable)
            rules = []
            channels = [channel] if channel else sorted({row.get("channel") for row in self.store.sellability_rules()})
            for candidate_channel in channels:
                if not candidate_channel:
                    continue
                rule = self._sellability_rule(candidate_channel, location_id)
                if not rule:
                    rules.append(
                        {
                            "channel": candidate_channel,
                            "eligible_location": False,
                            "sellable_units": 0,
                            "reason": "No sellability rule maps this channel to the location.",
                        }
                    )
                    continue
                eligible = rule.get("allow_sale", "true").lower() == "true"
                max_days = self.store.to_float(rule.get("max_days_to_expiry"), 999)
                expiry_blocked = 0.0
                reference_date = self.store.reference_time().date()
                for batch in self.store.inventory_batches():
                    if batch.get("sku") != sku or batch.get("location_id") != location_id:
                        continue
                    expiry = batch.get("expiry_date", "")
                    if expiry and max_days < 999:
                        try:
                            days = (datetime.date.fromisoformat(expiry) - reference_date).days
                            if days < max_days:
                                expiry_blocked += self.store.to_float(batch.get("quantity"))
                        except ValueError:
                            pass
                sellable = max(0.0, usable - expiry_blocked) if eligible else 0.0
                rules.append(
                    {
                        "channel": candidate_channel,
                        "eligible_location": eligible,
                        "sellable_units": int(sellable),
                        "max_days_to_expiry": int(max_days),
                        "expiry_blocked_units": int(expiry_blocked),
                        "reason": (
                            "Location is not mapped to this channel."
                            if not eligible
                            else "Near-expiry or blocked batch limits online sale."
                            if expiry_blocked
                            else "Location and batch state allow sale."
                        ),
                    }
                )
            locations.append(
                {
                    "location_id": location_id,
                    "physical_on_hand": int(physical),
                    "free_after_reservations": int(free),
                    "usable_units": int(usable),
                    "blocked_or_unusable_units": int(expired_or_blocked),
                    "channel_rules": rules,
                }
            )
        channel_records = self._channel_available(sku, channel)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "sku": sku,
            "product_name": product.get("product_name"),
            "channel_filter": channel,
            "physical_locations": locations,
            "channel_records": channel_records,
            "summary": {
                "physically_available": sum(item["physical_on_hand"] for item in locations),
                "usable_after_quality": sum(item["usable_units"] for item in locations),
                "channel_available": sum(item["available_qty"] for item in channel_records),
                "explanation": (
                    "Physical stock is not automatically sellable online; channel mapping, reservations, "
                    "batch quality, expiry, and recall state must all pass."
                ),
            },
        }

    def _allocation_conflicts(self) -> Dict[str, Any]:
        """Detect over-allocation and unsafe order commitments."""
        orders = {row.get("order_id"): row for row in self.store.customer_orders()}
        conflicts = []
        for allocation in self.store.order_allocations():
            order = orders.get(allocation.get("order_id"))
            if not order:
                continue
            sku = allocation.get("sku", "")
            location_id = allocation.get("location_id", "")
            allocated = self.store.to_int(allocation.get("allocated_qty"))
            available = int(self._usable(sku, location_id))
            shortfall = max(0, allocated - available)
            reason = allocation.get("conflict_reason", "")
            status = allocation.get("allocation_status", "")
            if shortfall <= 0 and status not in {"over_allocated", "conflict"} and not reason:
                continue
            alternate = []
            for (position_sku, other_location), position in self._positions().items():
                if position_sku != sku or other_location == location_id:
                    continue
                other_available = int(self._usable(sku, other_location))
                if other_available <= 0:
                    continue
                lane = self._lane(other_location, location_id)
                if not lane:
                    continue
                alternate.append(
                    {
                        "location_id": other_location,
                        "usable_units": other_available,
                        "transfer_lead_time_days": int(self.store.to_float(lane.get("lead_time_days"))),
                        "transfer_cost_per_unit_inr": self.store.to_float(lane.get("cost_per_unit_inr")),
                    }
                )
            alternate.sort(key=lambda item: item["transfer_lead_time_days"])
            unit_cost = self.store.to_float(self._product(sku).get("unit_cost"))
            conflicts.append(
                {
                    "order_id": allocation.get("order_id"),
                    "order_line_id": allocation.get("order_line_id"),
                    "sku": sku,
                    "location_id": location_id,
                    "order_quantity": self.store.to_int(order.get("quantity")),
                    "allocated_qty": allocated,
                    "available_usable_qty": available,
                    "shortfall_units": shortfall,
                    "allocation_status": status,
                    "conflict_reason": reason,
                    "financial_exposure_inr": round(shortfall * unit_cost, 2),
                    "alternate_locations": alternate,
                    "safe_resolution": (
                        f"Reduce allocation to {available} units and backorder {shortfall} units."
                        if shortfall > 0
                        else "Reallocate or release the unsafe commitment before fulfillment."
                    ),
                }
            )
        conflicts.sort(key=lambda item: item["financial_exposure_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "conflicts": len(conflicts),
                "units_at_risk": sum(item["shortfall_units"] for item in conflicts),
                "financial_exposure_inr": round(sum(item["financial_exposure_inr"] for item in conflicts), 2),
            },
            "conflicts": conflicts,
        }

    def _redistribute_plan(self, horizon_days: int) -> Dict[str, Any]:
        """Simulate pre-positioning inventory using regional demand and transfer lanes."""
        demand: Dict[Tuple[str, str], float] = {}
        reference = self.store.reference_time().date()
        start = (reference - datetime.timedelta(days=horizon_days - 1)).isoformat()
        for row in self.store.location_demand():
            if row.get("date", "") < start:
                continue
            key = (row.get("sku", ""), row.get("location_id", ""))
            demand[key] = demand.get(key, 0.0) + self.store.to_float(row.get("units"))
        positions = self._positions()
        recommendations = []
        for (sku, location_id), position in positions.items():
            usable = self._usable(sku, location_id)
            projected_demand = demand.get((sku, location_id), 0.0)
            target = projected_demand * 1.1
            surplus = max(0.0, usable - target)
            deficit = max(0.0, target - usable)
            if surplus <= 0 and deficit <= 0:
                continue
            recommendations.append(
                {
                    "sku": sku,
                    "location_id": location_id,
                    "usable_units": int(usable),
                    "projected_demand_units": int(projected_demand),
                    "surplus_units": int(surplus),
                    "deficit_units": int(deficit),
                }
            )
        transfers = []
        for surplus_row in [row for row in recommendations if row["surplus_units"] > 0]:
            for deficit_row in [row for row in recommendations if row["deficit_units"] > 0]:
                if surplus_row["sku"] != deficit_row["sku"]:
                    continue
                lane = self._lane(surplus_row["location_id"], deficit_row["location_id"])
                if not lane:
                    continue
                qty = min(
                    surplus_row["surplus_units"],
                    deficit_row["deficit_units"],
                    self.store.to_float(lane.get("max_transfer_qty")),
                )
                if qty < 1:
                    continue
                qty = int(qty)
                cost = qty * self.store.to_float(lane.get("cost_per_unit_inr"))
                transfers.append(
                    {
                        "sku": surplus_row["sku"],
                        "from_location_id": surplus_row["location_id"],
                        "to_location_id": deficit_row["location_id"],
                        "quantity": qty,
                        "transfer_cost_inr": round(cost, 2),
                        "lead_time_days": int(self.store.to_float(lane.get("lead_time_days"))),
                    }
                )
                surplus_row["surplus_units"] -= qty
                deficit_row["deficit_units"] -= qty
        transfers.sort(key=lambda item: (item["lead_time_days"], item["transfer_cost_inr"]))
        return {
            "as_of": self.store.reference_time().isoformat(),
            "horizon_days": horizon_days,
            "summary": {
                "locations_with_imbalance": len(recommendations),
                "recommended_transfers": len(transfers),
                "total_units_moved": sum(item["quantity"] for item in transfers),
                "total_transfer_cost_inr": round(sum(item["transfer_cost_inr"] for item in transfers), 2),
            },
            "recommendations": transfers,
            "location_balance": sorted(recommendations, key=lambda item: item["deficit_units"], reverse=True),
        }

    def _closure_simulation(self, location_id: str, horizon_days: int) -> Dict[str, Any]:
        """Model demand redistribution if a fulfillment center temporarily closes."""
        location_meta = self._location_meta()
        if location_id not in location_meta:
            return {"error": f"Unknown location: {location_id}"}
        reference = self.store.reference_time().date()
        start = (reference - datetime.timedelta(days=horizon_days - 1)).isoformat()
        closed_demand: Dict[str, float] = {}
        for row in self.store.location_demand():
            if row.get("location_id") != location_id or row.get("date", "") < start:
                continue
            closed_demand[row.get("sku", "")] = closed_demand.get(row.get("sku", ""), 0.0) + self.store.to_float(
                row.get("units")
            )
        capacity = self.store.fulfillment_capacity()
        alternate_locations = [
            candidate
            for candidate in capacity
            if candidate != location_id and capacity[candidate].get("active", "true").lower() == "true"
        ]
        total_capacity = sum(
            self.store.to_float(capacity[candidate].get("daily_units_capacity")) for candidate in alternate_locations
        )
        items = []
        total_lost_units = 0.0
        total_lost_revenue = 0.0
        for sku, demand_units in closed_demand.items():
            if demand_units <= 0:
                continue
            product = self._product(sku)
            unit_price = self.store.to_float(product.get("selling_price"))
            alternate_stock = sum(self._usable(sku, candidate) for candidate in alternate_locations)
            allocatable = min(demand_units, alternate_stock)
            lost_units = max(0.0, demand_units - allocatable)
            lost_revenue = lost_units * unit_price
            total_lost_units += lost_units
            total_lost_revenue += lost_revenue
            items.append(
                {
                    "sku": sku,
                    "closed_location_demand_units": int(demand_units),
                    "alternate_usable_stock_units": int(alternate_stock),
                    "allocatable_units": int(allocatable),
                    "projected_lost_units": int(lost_units),
                    "projected_lost_revenue_inr": round(lost_revenue, 2),
                    "needs_replenishment_units": int(lost_units),
                }
            )
        daily_demand = sum(closed_demand.values()) / horizon_days if horizon_days > 0 else 0.0
        return {
            "as_of": self.store.reference_time().isoformat(),
            "closed_location_id": location_id,
            "closed_location_name": location_meta[location_id].get("location_name"),
            "horizon_days": horizon_days,
            "summary": {
                "daily_demand_units": round(daily_demand, 2),
                "alternate_daily_capacity_units": int(total_capacity),
                "capacity_sufficient": total_capacity >= daily_demand,
                "projected_lost_units": int(total_lost_units),
                "projected_lost_revenue_inr": round(total_lost_revenue, 2),
                "recommended_action": (
                    "Redirect demand to alternate locations and expedite replenishment for the shortfall."
                    if total_lost_units > 0
                    else "Alternate locations can absorb demand within current capacity."
                ),
            },
            "sku_impact": sorted(items, key=lambda item: item["projected_lost_revenue_inr"], reverse=True),
            "alternate_locations": alternate_locations,
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Execute a multi-location fulfillment action."""
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = self._normalize_action(args.get("action", "location_stockout_risk"))
        sku = self.store.normalize_optional(args.get("sku"))
        channel = self.store.normalize_optional(args.get("channel"))
        location_id = self.store.normalize_optional(args.get("location_id"))
        destination = self.store.normalize_optional(args.get("destination_location_id"))
        order_id = self.store.normalize_optional(args.get("order_id"))
        quantity = self.store.to_int(args.get("quantity"))
        horizon = self.store.to_int(args.get("horizon_days"), 30)

        if action == "fulfill_options":
            return self._fulfill_options(sku or "", quantity, destination, order_id)
        if action == "location_stockout_risk":
            return self._location_stockout_risk(horizon)
        if action == "transfer_candidates":
            return self._transfer_candidates(sku, location_id)
        if action == "in_transit":
            return self._in_transit(sku)
        if action == "sellability_check":
            if not sku:
                return {"error": "sellability_check requires a sku"}
            return self._sellability_check(sku, channel)
        if action == "allocation_conflicts":
            return self._allocation_conflicts()
        if action == "redistribute_plan":
            return self._redistribute_plan(horizon)
        if action == "closure_simulation":
            return self._closure_simulation(location_id or "WH-01", horizon)
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
