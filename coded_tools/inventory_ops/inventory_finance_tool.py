"""
Inventory finance CodedTool for Neuro-SAN.
Calculates capital, gross and net profit, carrying cost, stockout impact,
budget-versus-hold trade-offs, supplier cost impact, service-level scenarios,
and policy savings with explicit assumptions.
"""

import asyncio
import datetime
import logging
import math
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore
from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool
from coded_tools.inventory_ops.replenishment_engine_tool import ReplenishmentEngineTool

logger = logging.getLogger(__name__)


class InventoryFinanceTool(CodedTool):
    """Deterministic financial reasoning engine for inventory decisions."""

    def __init__(self) -> None:
        """Initialize with shared analytics and replenishment dependencies."""
        self.store = InventoryDataStore()
        self.analytics = InventoryAnalyticsTool()
        self.replenishment = ReplenishmentEngineTool()
        logger.info("%s initialized.", self.__class__.__name__)

    @staticmethod
    def _normalize_action(action: str) -> str:
        """Map natural-language action labels onto supported actions."""
        normalized = str(action).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "capital": "capital_tied_up",
            "inventory_value": "capital_tied_up",
            "cash_tied_up": "capital_tied_up",
            "gross_profit": "gross_profit_ranking",
            "profit_ranking": "gross_profit_ranking",
            "carrying_cost": "excess_carrying_cost",
            "excess_cost": "excess_carrying_cost",
            "stockout": "stockout_impact",
            "stockout_cost": "stockout_impact",
            "budget": "budget_vs_hold",
            "hold_cash": "budget_vs_hold",
            "supplier_cost": "supplier_cost_impact",
            "landed_cost": "supplier_cost_impact",
            "service_level": "service_level_simulation",
            "inventory_reduction": "service_level_simulation",
            "policy": "policy_savings",
            "savings": "policy_savings",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "capital" in normalized or "tied" in normalized:
            return "capital_tied_up"
        if "profit" in normalized or "margin" in normalized:
            return "gross_profit_ranking"
        if "carrying" in normalized or "holding" in normalized:
            return "excess_carrying_cost"
        if "stockout" in normalized:
            return "stockout_impact"
        if "budget" in normalized or "hold" in normalized:
            return "budget_vs_hold"
        if "supplier" in normalized or "landed" in normalized:
            return "supplier_cost_impact"
        if "service" in normalized or "reduction" in normalized:
            return "service_level_simulation"
        if "policy" in normalized or "saving" in normalized:
            return "policy_savings"
        return normalized

    def _policy(self) -> Dict[str, str]:
        """Return the current cost policy."""
        rows = self.store.load("cost_policy.csv")
        return rows[0] if rows else {}

    def _analytics_items(self) -> List[Dict[str, Any]]:
        """Return analytics items for all SKUs."""
        result = self.analytics.invoke({"action": "audit_all"}, {})
        if isinstance(result, str):
            return []
        return result.get("items", [])

    def _daily_stats(self, sku: str, window: int = 28) -> Tuple[float, float]:
        """Return mean and standard deviation of daily fulfilled sales."""
        rows = [row for row in self.store.sales() if row.get("sku") == sku and row.get("status") == "fulfilled"]
        by_day: Dict[str, float] = {}
        for row in rows:
            day = row.get("order_date", "")[:10]
            by_day[day] = by_day.get(day, 0.0) + self.store.to_float(row.get("quantity"))
        if not by_day:
            return 0.0, 0.0
        latest = max(datetime.date.fromisoformat(day) for day in by_day)
        start = latest - datetime.timedelta(days=window - 1)
        values = [by_day.get((start + datetime.timedelta(days=offset)).isoformat(), 0.0) for offset in range(window)]
        mean = sum(values) / len(values)
        if len(values) < 2:
            return mean, max(mean * 0.5, 0.5)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        return mean, max(variance**0.5, 0.5)

    @staticmethod
    def _normal_cdf(z_score: float) -> float:
        """Return the standard normal cumulative probability."""
        return 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))

    def _freight_by_sku(self) -> Dict[str, float]:
        """Allocate actual freight and handling to SKUs using PO quantities."""
        freight_rows = {row.get("po_id"): row for row in self.store.freight_costs()}
        allocated: Dict[str, float] = {}
        for po in self.store.load("purchase_orders.csv"):
            po_id = po.get("po_id")
            freight = freight_rows.get(po_id, {})
            extra = sum(
                self.store.to_float(freight.get(field))
                for field in ["freight_inr", "duties_inr", "handling_inr", "shortage_penalty_inr"]
            )
            sku = po.get("sku", "")
            allocated[sku] = allocated.get(sku, 0.0) + extra
        return allocated

    def _sales_finance(self, sku: str, window_days: int = 90) -> Dict[str, float]:
        """Calculate revenue, COGS, gross profit, refunds, and net profit for a SKU."""
        product = self.store.products().get(sku, {})
        unit_cost = self.store.to_float(product.get("unit_cost"))
        rows = [row for row in self.store.sales() if row.get("sku") == sku and row.get("status") == "fulfilled"]
        if rows:
            latest = max(datetime.date.fromisoformat(row.get("order_date", "")[:10]) for row in rows)
            start = (latest - datetime.timedelta(days=window_days - 1)).isoformat()
            rows = [row for row in rows if row.get("order_date", "")[:10] >= start]
        revenue = 0.0
        units = 0.0
        for row in rows:
            quantity = self.store.to_float(row.get("quantity"))
            price = self.store.to_float(
                row.get("unit_selling_price"), self.store.to_float(product.get("selling_price"))
            )
            revenue += quantity * price
            units += quantity
        cogs = units * unit_cost
        gross_profit = revenue - cogs
        returns = [row for row in self.store.load("returns.csv") if row.get("sku") == sku]
        return_units = sum(self.store.to_float(row.get("quantity")) for row in returns)
        refund_value = return_units * self.store.to_float(product.get("selling_price"))
        freight = self._freight_by_sku().get(sku, 0.0)
        net_profit = gross_profit - refund_value - freight
        return {
            "units_sold": units,
            "revenue_inr": revenue,
            "cogs_inr": cogs,
            "gross_profit_inr": gross_profit,
            "return_units": return_units,
            "refund_value_inr": refund_value,
            "allocated_freight_inr": freight,
            "net_profit_inr": net_profit,
        }

    def _capital_tied_up(self) -> Dict[str, Any]:
        """Calculate inventory value using unit cost as the accounting basis."""
        items = []
        total_value = 0.0
        sellable_value = 0.0
        unusable_value = 0.0
        by_location: Dict[str, float] = {}
        by_category: Dict[str, float] = {}
        for item in self._analytics_items():
            sku = item.get("sku", "")
            product = self.store.products().get(sku, {})
            unit_cost = self.store.to_float(product.get("unit_cost"))
            on_hand = self.store.to_float(item.get("inventory", {}).get("on_hand"))
            value = on_hand * unit_cost
            batch_sellable = 0.0
            batch_unusable = 0.0
            for batch in self.store.inventory_batches():
                if batch.get("sku") != sku:
                    continue
                quantity = self.store.to_float(batch.get("quantity"))
                status = batch.get("quality_status", "").lower()
                if status == "sellable" and not batch.get("recall_id"):
                    batch_sellable += quantity
                else:
                    batch_unusable += quantity
            total_value += value
            sellable_value += batch_sellable * unit_cost
            unusable_value += batch_unusable * unit_cost
            by_category[product.get("category", "Unknown")] = (
                by_category.get(product.get("category", "Unknown"), 0.0) + value
            )
            for location in item.get("inventory", {}).get("locations", []):
                location_id = location.get("location_id", "")
                by_location[location_id] = (
                    by_location.get(location_id, 0.0) + self.store.to_float(location.get("on_hand")) * unit_cost
                )
            items.append(
                {
                    "sku": sku,
                    "product_name": product.get("product_name"),
                    "on_hand_units": int(on_hand),
                    "unit_cost_inr": unit_cost,
                    "inventory_value_inr": round(value, 2),
                    "sellable_value_inr": round(batch_sellable * unit_cost, 2),
                    "unusable_value_inr": round(batch_unusable * unit_cost, 2),
                }
            )
        items.sort(key=lambda item: item["inventory_value_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "headline": f"Inventory value at cost is INR {total_value:,.2f}.",
            "accounting_basis": "on-hand units x product unit cost; inbound and reserved units are excluded",
            "summary": {
                "total_inventory_value_inr": round(total_value, 2),
                "sellable_value_inr": round(sellable_value, 2),
                "unusable_value_inr": round(unusable_value, 2),
                "by_location": {key: round(value, 2) for key, value in by_location.items()},
                "by_category": {key: round(value, 2) for key, value in by_category.items()},
            },
            "items": items,
        }

    def _gross_profit_ranking(self, window_days: int) -> Dict[str, Any]:
        """Rank products by gross profit and show net profit after returns and freight."""
        items = []
        for sku, product in self.store.products().items():
            finance = self._sales_finance(sku, window_days)
            revenue = finance["revenue_inr"]
            gross_profit = finance["gross_profit_inr"]
            items.append(
                {
                    "sku": sku,
                    "product_name": product.get("product_name"),
                    **{key: round(value, 2) for key, value in finance.items()},
                    "gross_margin_pct": round((gross_profit / revenue) * 100, 1) if revenue else 0.0,
                    "net_margin_pct": (round((finance["net_profit_inr"] / revenue) * 100, 1) if revenue else 0.0),
                }
            )
        items.sort(key=lambda item: item["gross_profit_inr"], reverse=True)
        highest = items[0] if items else None
        return {
            "as_of": self.store.reference_time().isoformat(),
            "headline": (
                f"Highest gross profit: {highest.get('sku')} at INR {highest.get('gross_profit_inr'):,.2f}."
                if highest
                else "No sales found in the requested window."
            ),
            "window_days": window_days,
            "basis": "Gross profit = revenue - COGS. Net profit also subtracts refunds and allocated freight.",
            "summary": {
                "highest_gross_profit": items[0] if items else None,
                "lowest_gross_profit": items[-1] if items else None,
                "total_gross_profit_inr": round(sum(item["gross_profit_inr"] for item in items), 2),
                "total_net_profit_inr": round(sum(item["net_profit_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _excess_carrying_cost(self, period_days: int) -> Dict[str, Any]:
        """Estimate the carrying cost of excess inventory."""
        policy = self._policy()
        holding_rate = self.store.to_float(policy.get("holding_rate_pct"), 22.0)
        replenishment_policy = self.store.load("replenishment_policy.csv")
        excess_dos = (
            self.store.to_float(replenishment_policy[0].get("excess_dos"), 45.0) if replenishment_policy else 45.0
        )
        items = []
        total_excess_value = 0.0
        for item in self._analytics_items():
            dos = self.store.to_float(item.get("metrics", {}).get("days_of_supply"))
            velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
            usable = self.store.to_float(item.get("inventory", {}).get("usable_on_hand"))
            unit_cost = self.store.to_float(item.get("unit_cost"))
            if dos <= excess_dos or usable <= 0:
                continue
            target_units = velocity * excess_dos
            excess_units = max(0.0, usable - target_units)
            excess_value = excess_units * unit_cost
            annual_cost = excess_value * (holding_rate / 100.0)
            period_cost = annual_cost * (period_days / 365.0)
            total_excess_value += excess_value
            items.append(
                {
                    "sku": item.get("sku"),
                    "product_name": item.get("product_name"),
                    "days_of_supply": dos,
                    "excess_units_above_target": int(excess_units),
                    "excess_value_inr": round(excess_value, 2),
                    "annual_carrying_cost_inr": round(annual_cost, 2),
                    "period_carrying_cost_inr": round(period_cost, 2),
                }
            )
        items.sort(key=lambda item: item["annual_carrying_cost_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "assumptions": {
                "holding_rate_pct": holding_rate,
                "excess_dos_threshold": excess_dos,
                "period_days": period_days,
            },
            "summary": {
                "excess_skus": len(items),
                "total_excess_value_inr": round(total_excess_value, 2),
                "annual_carrying_cost_inr": round(sum(item["annual_carrying_cost_inr"] for item in items), 2),
                "period_carrying_cost_inr": round(sum(item["period_carrying_cost_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _stockout_impact(self, sku: Optional[str], horizon_days: int) -> Dict[str, Any]:
        """Estimate missed sales and margin from projected stockouts."""
        items = []
        total_lost_revenue = 0.0
        total_lost_margin = 0.0
        for item in self._analytics_items():
            item_sku = item.get("sku", "")
            if sku and item_sku != sku:
                continue
            velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
            net_position = self.store.to_float(item.get("inventory", {}).get("net_position"))
            selling_price = self.store.to_float(item.get("selling_price"))
            unit_margin = self.store.to_float(item.get("unit_gross_margin"))
            projected_demand = velocity * horizon_days
            lost_units = max(0.0, projected_demand - net_position)
            lost_revenue = lost_units * selling_price
            lost_margin = lost_units * unit_margin
            total_lost_revenue += lost_revenue
            total_lost_margin += lost_margin
            items.append(
                {
                    "sku": item_sku,
                    "product_name": item.get("product_name"),
                    "daily_velocity": round(velocity, 2),
                    "net_position_units": int(net_position),
                    "projected_demand_units": int(projected_demand),
                    "projected_unmet_units": int(lost_units),
                    "lost_revenue_inr": round(lost_revenue, 2),
                    "lost_gross_margin_inr": round(lost_margin, 2),
                }
            )
        items.sort(key=lambda item: item["lost_gross_margin_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "horizon_days": horizon_days,
            "assumptions": [
                "Demand is the recent uncensored daily velocity.",
                "No substitute purchase and no expedited replenishment are assumed.",
                "Lost gross margin uses current unit selling price minus unit cost.",
            ],
            "summary": {
                "skus_at_risk": sum(1 for item in items if item["projected_unmet_units"] > 0),
                "total_projected_lost_revenue_inr": round(total_lost_revenue, 2),
                "total_projected_lost_margin_inr": round(total_lost_margin, 2),
            },
            "items": items,
        }

    def _budget_vs_hold(self, budget_limit: float, horizon_days: int) -> Dict[str, Any]:
        """Compare spending the budget on replenishment against holding cash."""
        policy = self._policy()
        holding_rate = self.store.to_float(policy.get("holding_rate_pct"), 22.0)
        allocation = self.replenishment.invoke({"action": "budget_allocate", "budget_limit": budget_limit}, {})
        if isinstance(allocation, str):
            return {"error": "Failed to allocate budget"}
        spent = self.store.to_float(allocation.get("budget_spent_inr"))
        protected_margin = self.store.to_float(allocation.get("protected_margin_inr"))
        remaining = self.store.to_float(allocation.get("remaining_budget_inr"))
        holding_cost_avoided = spent * (holding_rate / 100.0) * (horizon_days / 365.0)
        net_benefit = protected_margin - holding_cost_avoided
        return {
            "as_of": self.store.reference_time().isoformat(),
            "headline": (
                "Replenish; expected protected margin exceeds the carrying-cost trade-off."
                if net_benefit > 0
                else "Hold cash; the replenishment case does not cover the assumed carrying cost."
            ),
            "budget_limit_inr": budget_limit,
            "replenishment_scenario": {
                "spend_inr": round(spent, 2),
                "remaining_cash_inr": round(remaining, 2),
                "protected_margin_inr": round(protected_margin, 2),
                "holding_cost_avoided_inr": round(holding_cost_avoided, 2),
                "net_expected_benefit_inr": round(net_benefit, 2),
                "allocated_items": allocation.get("allocated", []),
            },
            "hold_cash_scenario": {
                "cash_retained_inr": round(budget_limit, 2),
                "margin_at_risk_inr": round(protected_margin, 2),
                "holding_cost_incurred_inr": 0.0,
                "net_expected_benefit_inr": 0.0,
            },
            "assumptions": {
                "holding_rate_pct": holding_rate,
                "horizon_days": horizon_days,
                "protected_margin": "Gross margin protected by avoiding projected stockouts.",
            },
            "recommendation": (
                "Replenish; expected protected margin exceeds the carrying-cost trade-off."
                if net_benefit > 0
                else "Hold cash; the replenishment case does not cover the assumed carrying cost."
            ),
        }

    def _supplier_cost_impact(self) -> Dict[str, Any]:
        """Rank suppliers by landed-cost impact from price, freight, and shortages."""
        suppliers = {row.get("supplier_id"): row for row in self.store.load("suppliers.csv")}
        freight = {row.get("po_id"): row for row in self.store.freight_costs()}
        receipts = self.store.load("receipts.csv")
        groups: Dict[str, Dict[str, Any]] = {}
        for po in self.store.load("purchase_orders.csv"):
            supplier_id = po.get("supplier_id", "")
            group = groups.setdefault(
                supplier_id,
                {
                    "supplier_id": supplier_id,
                    "supplier_name": suppliers.get(supplier_id, {}).get("supplier_name"),
                    "purchase_spend_inr": 0.0,
                    "freight_inr": 0.0,
                    "duties_inr": 0.0,
                    "handling_inr": 0.0,
                    "shortage_penalty_inr": 0.0,
                    "ordered_units": 0,
                    "received_units": 0,
                },
            )
            quantity = self.store.to_float(po.get("requested_qty"))
            unit_cost = self.store.to_float(po.get("unit_cost"))
            group["purchase_spend_inr"] += quantity * unit_cost
            group["ordered_units"] += int(quantity)
            cost_row = freight.get(po.get("po_id"), {})
            group["freight_inr"] += self.store.to_float(cost_row.get("freight_inr"))
            group["duties_inr"] += self.store.to_float(cost_row.get("duties_inr"))
            group["handling_inr"] += self.store.to_float(cost_row.get("handling_inr"))
            group["shortage_penalty_inr"] += self.store.to_float(cost_row.get("shortage_penalty_inr"))
        for receipt in receipts:
            supplier_id = next(
                (
                    po.get("supplier_id")
                    for po in self.store.load("purchase_orders.csv")
                    if po.get("po_id") == receipt.get("po_id")
                ),
                None,
            )
            if supplier_id in groups:
                groups[supplier_id]["received_units"] += self.store.to_int(receipt.get("received_qty"))
        items = []
        for group in groups.values():
            extras = group["freight_inr"] + group["duties_inr"] + group["handling_inr"] + group["shortage_penalty_inr"]
            total_landed = group["purchase_spend_inr"] + extras
            supplier = suppliers.get(group["supplier_id"], {})
            items.append(
                {
                    **group,
                    "landed_extras_inr": round(extras, 2),
                    "total_landed_cost_inr": round(total_landed, 2),
                    "landed_extra_pct": (
                        round((extras / group["purchase_spend_inr"]) * 100, 2) if group["purchase_spend_inr"] else 0.0
                    ),
                    "shortfall_units": max(0, group["ordered_units"] - group["received_units"]),
                    "on_time_delivery_pct": self.store.to_float(supplier.get("on_time_delivery_pct")),
                    "quality_reject_pct": self.store.to_float(supplier.get("quality_reject_pct")),
                }
            )
        items.sort(key=lambda item: item["landed_extras_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "basis": "Purchase spend plus actual freight, duties, handling, and shortage penalties.",
            "summary": {
                "suppliers_evaluated": len(items),
                "highest_landed_extra": items[0] if items else None,
                "total_landed_extras_inr": round(sum(item["landed_extras_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _service_level_simulation(self, reduction_percent: float) -> Dict[str, Any]:
        """Simulate the service-level and investment effect of an inventory reduction."""
        items = []
        total_value = 0.0
        reduced_value = 0.0
        weighted_before = 0.0
        weighted_after = 0.0
        total_weight = 0.0
        for item in self._analytics_items():
            sku = item.get("sku", "")
            usable = self.store.to_float(item.get("inventory", {}).get("usable_on_hand"))
            unit_cost = self.store.to_float(item.get("unit_cost"))
            lead_time = self.store.to_float(item.get("metrics", {}).get("lead_time_days"), 7.0)
            velocity, std_dev = self._daily_stats(sku)
            if velocity <= 0:
                continue
            new_usable = usable * (1.0 - reduction_percent / 100.0)
            lead_time_demand = velocity * lead_time
            denominator = max(std_dev * math.sqrt(lead_time), 0.01)
            before_z = (usable - lead_time_demand) / denominator
            after_z = (new_usable - lead_time_demand) / denominator
            before_service = self._normal_cdf(before_z)
            after_service = self._normal_cdf(after_z)
            value = usable * unit_cost
            weight = max(velocity * self.store.to_float(item.get("selling_price")), 1.0)
            total_value += value
            reduced_value += new_usable * unit_cost
            total_weight += weight
            weighted_before += before_service * weight
            weighted_after += after_service * weight
            items.append(
                {
                    "sku": sku,
                    "usable_units_before": int(usable),
                    "usable_units_after": int(new_usable),
                    "inventory_value_reduction_inr": round((usable - new_usable) * unit_cost, 2),
                    "service_level_before_pct": round(before_service * 100, 1),
                    "service_level_after_pct": round(after_service * 100, 1),
                    "service_level_change_pct": round((after_service - before_service) * 100, 1),
                }
            )
        items.sort(key=lambda item: item["service_level_change_pct"])
        return {
            "as_of": self.store.reference_time().isoformat(),
            "reduction_percent": reduction_percent,
            "assumptions": [
                "Usable on-hand inventory is reduced uniformly.",
                "Demand is normally distributed with recent mean and standard deviation.",
                "Service level is the probability of covering demand during supplier lead time.",
            ],
            "summary": {
                "inventory_value_before_inr": round(total_value, 2),
                "inventory_value_after_inr": round(reduced_value, 2),
                "investment_reduction_inr": round(total_value - reduced_value, 2),
                "weighted_service_level_before_pct": (
                    round((weighted_before / total_weight) * 100, 1) if total_weight else 0.0
                ),
                "weighted_service_level_after_pct": (
                    round((weighted_after / total_weight) * 100, 1) if total_weight else 0.0
                ),
            },
            "items": items,
        }

    def _policy_savings(self, period_days: int) -> Dict[str, Any]:
        """Compare current policy outcomes against a baseline policy."""
        policies = self.store.policy_history()
        current = next((row for row in policies if row.get("effective_to", "") == ""), None)
        baseline = next((row for row in policies if row.get("baseline_flag", "").lower() == "true"), None)
        if not current or not baseline:
            return {"error": "Policy history requires one baseline and one current policy"}
        capital = self._capital_tied_up()
        inventory_value = self.store.to_float(capital["summary"]["total_inventory_value_inr"])
        baseline_rate = self.store.to_float(baseline.get("holding_rate_pct"))
        current_rate = self.store.to_float(current.get("holding_rate_pct"))
        baseline_cost = inventory_value * (baseline_rate / 100.0) * (period_days / 365.0)
        current_cost = inventory_value * (current_rate / 100.0) * (period_days / 365.0)
        savings = baseline_cost - current_cost
        demand_change = 0.0
        for sku in self.store.products():
            mean, _ = self._daily_stats(sku, 28)
            previous_rows = [
                row for row in self.store.sales() if row.get("sku") == sku and row.get("status") == "fulfilled"
            ]
            if not previous_rows:
                continue
            latest = max(datetime.date.fromisoformat(row.get("order_date", "")[:10]) for row in previous_rows)
            previous_start = (latest - datetime.timedelta(days=55)).isoformat()
            previous_end = (latest - datetime.timedelta(days=28)).isoformat()
            previous_units = sum(
                self.store.to_float(row.get("quantity"))
                for row in previous_rows
                if previous_start <= row.get("order_date", "")[:10] <= previous_end
            )
            if previous_units > 0:
                demand_change += (mean * 28.0 - previous_units) / previous_units
        demand_change_pct = round((demand_change / max(len(self.store.products()), 1)) * 100, 1)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "period_days": period_days,
            "policies": {
                "baseline": baseline,
                "current": current,
            },
            "financials": {
                "inventory_value_inr": round(inventory_value, 2),
                "baseline_holding_cost_inr": round(baseline_cost, 2),
                "current_holding_cost_inr": round(current_cost, 2),
                "estimated_savings_inr": round(savings, 2),
                "savings_pct_of_baseline": (round((savings / baseline_cost) * 100, 1) if baseline_cost else 0.0),
            },
            "service_level": {
                "baseline_z": self.store.to_float(baseline.get("service_level_z")),
                "current_z": self.store.to_float(current.get("service_level_z")),
                "note": "A higher z implies a higher target cycle service level and usually more safety stock.",
            },
            "confounders": [
                f"Demand changed by approximately {demand_change_pct}% between the two 28-day windows.",
                "Promotions, stockouts, and supplier lead-time changes can affect the comparison.",
                "The calculation holds the current inventory value constant for both policies.",
            ],
            "confidence": "MEDIUM",
            "caveat": "This is an estimate, not a controlled experiment; validate with a longer clean baseline.",
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Execute an inventory finance action."""
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = self._normalize_action(args.get("action", "capital_tied_up"))
        sku = self.store.normalize_optional(args.get("sku"))
        horizon = self.store.to_int(args.get("horizon_days"), 30)
        period = self.store.to_int(args.get("period_days"), 30)
        budget = self.store.to_float(args.get("budget_limit"), 30000.0)
        reduction = self.store.to_float(args.get("reduction_percent"), 10.0)

        if action == "capital_tied_up":
            result = self._capital_tied_up()
        elif action == "gross_profit_ranking":
            result = self._gross_profit_ranking(horizon)
        elif action == "excess_carrying_cost":
            result = self._excess_carrying_cost(period)
        elif action == "stockout_impact":
            result = self._stockout_impact(sku, horizon)
        elif action == "budget_vs_hold":
            result = self._budget_vs_hold(budget, horizon)
        elif action == "supplier_cost_impact":
            result = self._supplier_cost_impact()
        elif action == "service_level_simulation":
            result = self._service_level_simulation(reduction)
        elif action == "policy_savings":
            result = self._policy_savings(period)
        else:
            return {"error": f"Unknown action: {action}"}
        if isinstance(result, dict):
            result["action"] = action
        return result

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
