"""
Planning scenario CodedTool for Neuro-SAN.
Simulates demand, lead-time, budget, discontinuation, wholesale, seasonal,
service-versus-cost, and supplier-failure scenarios.
"""

import asyncio
import datetime
import logging
import math
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore
from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool
from coded_tools.inventory_ops.replenishment_engine_tool import ReplenishmentEngineTool

logger = logging.getLogger(__name__)


class PlanningScenarioTool(CodedTool):
    """Deterministic planning and what-if engine for inventory decisions."""

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
            "demand_increase": "demand_surge",
            "demand_rise": "demand_surge",
            "lead_time": "lead_time_double",
            "lead_time_shock": "lead_time_double",
            "budget_reduction": "budget_cut",
            "budget": "budget_cut",
            "discontinue": "discontinue_slow",
            "slow_movers": "discontinue_slow",
            "wholesale": "wholesale_order",
            "large_order": "wholesale_order",
            "seasonal": "seasonal_plan",
            "season": "seasonal_plan",
            "tradeoff": "service_vs_cost",
            "service_cost": "service_vs_cost",
            "service_level": "service_level_simulation",
            "service_level_simulation": "service_level_simulation",
            "inventory_reduction": "service_level_simulation",
            "supplier_failure": "supplier_failure",
            "supplier_down": "supplier_failure",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "demand" in normalized:
            return "demand_surge"
        if "lead" in normalized:
            return "lead_time_double"
        if "budget" in normalized:
            return "budget_cut"
        if "discontinu" in normalized or "slow" in normalized:
            return "discontinue_slow"
        if "wholesale" in normalized or "large_order" in normalized:
            return "wholesale_order"
        if "season" in normalized:
            return "seasonal_plan"
        if "trade" in normalized or "service" in normalized:
            return "service_vs_cost"
        if "supplier" in normalized or "failure" in normalized:
            return "supplier_failure"
        return normalized

    def _items(self) -> List[Dict[str, Any]]:
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

    def _demand_surge(self, demand_change_percent: float, horizon_days: int) -> Dict[str, Any]:
        """Recompute shortages and purchasing under a demand change."""
        items = []
        total_shortage = 0.0
        total_purchase_cost = 0.0
        total_lost_margin = 0.0
        for item in self._items():
            sku = item.get("sku", "")
            velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
            net_position = self.store.to_float(item.get("inventory", {}).get("net_position"))
            unit_cost = self.store.to_float(item.get("unit_cost"))
            unit_margin = self.store.to_float(item.get("unit_gross_margin"))
            stressed_velocity = velocity * (1.0 + demand_change_percent / 100.0)
            projected_demand = stressed_velocity * horizon_days
            shortage = max(0.0, projected_demand - net_position)
            purchase_units = math.ceil(shortage)
            purchase_cost = purchase_units * unit_cost
            lost_margin = shortage * unit_margin
            total_shortage += shortage
            total_purchase_cost += purchase_cost
            total_lost_margin += lost_margin
            items.append(
                {
                    "sku": sku,
                    "product_name": item.get("product_name"),
                    "baseline_daily_velocity": round(velocity, 2),
                    "stressed_daily_velocity": round(stressed_velocity, 2),
                    "projected_demand_units": int(projected_demand),
                    "net_position_units": int(net_position),
                    "shortage_units": int(shortage),
                    "purchase_units_needed": purchase_units,
                    "purchase_cost_inr": round(purchase_cost, 2),
                    "lost_margin_if_uncovered_inr": round(lost_margin, 2),
                }
            )
        items.sort(key=lambda item: item["lost_margin_if_uncovered_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "demand_surge",
            "demand_change_percent": demand_change_percent,
            "horizon_days": horizon_days,
            "assumptions": ["Demand changes uniformly across SKUs.", "Supplier lead times are unchanged."],
            "summary": {
                "total_shortage_units": int(total_shortage),
                "total_purchase_cost_inr": round(total_purchase_cost, 2),
                "total_lost_margin_if_uncovered_inr": round(total_lost_margin, 2),
            },
            "items": items,
        }

    def _lead_time_double(self, multiplier: float, horizon_days: int) -> Dict[str, Any]:
        """Recompute stockout risk and safety stock under a lead-time shock."""
        items = []
        total_lost_margin = 0.0
        for item in self._items():
            sku = item.get("sku", "")
            velocity, std_dev = self._daily_stats(sku)
            usable = self.store.to_float(item.get("inventory", {}).get("usable_on_hand"))
            incoming = self.store.to_float(item.get("inventory", {}).get("incoming"))
            lead_time = self.store.to_float(item.get("metrics", {}).get("lead_time_days"), 7.0)
            stressed_lead_time = lead_time * multiplier
            lead_time_demand = velocity * stressed_lead_time
            safety_stock = math.ceil(1.65 * std_dev * math.sqrt(stressed_lead_time))
            required_position = math.ceil(lead_time_demand + safety_stock)
            gap = max(0.0, required_position - (usable + incoming))
            days_of_supply = (usable + incoming) / velocity if velocity > 0 else 999.0
            lost_margin = gap * self.store.to_float(item.get("unit_gross_margin"))
            total_lost_margin += lost_margin
            items.append(
                {
                    "sku": sku,
                    "product_name": item.get("product_name"),
                    "baseline_lead_time_days": int(lead_time),
                    "stressed_lead_time_days": int(stressed_lead_time),
                    "daily_velocity": round(velocity, 2),
                    "usable_plus_incoming_units": int(usable + incoming),
                    "stressed_lead_time_demand_units": int(lead_time_demand),
                    "required_safety_stock_units": safety_stock,
                    "additional_position_needed_units": int(gap),
                    "days_of_supply": round(days_of_supply, 1),
                    "lost_margin_if_uncovered_inr": round(lost_margin, 2),
                }
            )
        items.sort(key=lambda item: item["additional_position_needed_units"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "lead_time_double",
            "lead_time_multiplier": multiplier,
            "horizon_days": horizon_days,
            "assumptions": ["Lead time scales uniformly.", "Demand variability is unchanged."],
            "summary": {
                "skus_needing_more_position": sum(1 for item in items if item["additional_position_needed_units"] > 0),
                "total_additional_units_needed": sum(item["additional_position_needed_units"] for item in items),
                "total_lost_margin_if_uncovered_inr": round(total_lost_margin, 2),
            },
            "items": items,
        }

    def _budget_cut(self, budget_change_percent: float, budget_limit: float) -> Dict[str, Any]:
        """Rank funded and deferred purchases under a smaller budget."""
        new_budget = budget_limit * (1.0 + budget_change_percent / 100.0)
        allocation = self.replenishment.invoke({"action": "budget_allocate", "budget_limit": max(0.0, new_budget)}, {})
        queue = self.replenishment.invoke({"action": "queue", "limit": 20}, {})
        if isinstance(allocation, str) or isinstance(queue, str):
            return {"error": "Failed to build budget scenario"}
        funded = allocation.get("allocated", [])
        funded_skus = {item.get("sku") for item in funded}
        deferred = [item for item in queue.get("items", []) if item.get("sku") not in funded_skus]
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "budget_cut",
            "original_budget_inr": budget_limit,
            "budget_change_percent": budget_change_percent,
            "revised_budget_inr": round(max(0.0, new_budget), 2),
            "summary": {
                "funded_items": len(funded),
                "deferred_items": len(deferred),
                "spent_inr": round(self.store.to_float(allocation.get("budget_spent_inr")), 2),
                "remaining_inr": round(self.store.to_float(allocation.get("remaining_budget_inr")), 2),
                "protected_margin_inr": round(self.store.to_float(allocation.get("protected_margin_inr")), 2),
            },
            "funded": funded,
            "deferred": deferred,
            "priority_basis": "Protected gross margin, risk level, and supplier-compliant case packs.",
        }

    def _discontinue_slow(self, limit: int) -> Dict[str, Any]:
        """Assess inventory and obligations for the slowest-selling products."""
        items = []
        for item in self._items():
            sku = item.get("sku", "")
            velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
            usable = self.store.to_float(item.get("inventory", {}).get("usable_on_hand"))
            unit_cost = self.store.to_float(item.get("unit_cost"))
            selling_price = self.store.to_float(item.get("selling_price"))
            open_pos = [row for row in self.store.open_purchase_orders() if row.get("sku") == sku]
            open_units = sum(self.store.to_float(row.get("requested_qty")) for row in open_pos)
            items.append(
                {
                    "sku": sku,
                    "product_name": item.get("product_name"),
                    "daily_velocity": round(velocity, 2),
                    "usable_units": int(usable),
                    "trapped_capital_inr": round(usable * unit_cost, 2),
                    "open_po_units": int(open_units),
                    "open_po_value_inr": round(
                        sum(
                            self.store.to_float(row.get("requested_qty")) * self.store.to_float(row.get("unit_cost"))
                            for row in open_pos
                        ),
                        2,
                    ),
                    "clearance_value_at_20pct_off_inr": round(usable * selling_price * 0.8, 2),
                    "recommended_action": (
                        "Stop replenishment, cancel or reduce open POs, and clear remaining stock."
                        if open_units > 0
                        else "Stop replenishment and clear remaining stock."
                    ),
                }
            )
        items.sort(key=lambda item: (item["daily_velocity"], -item["trapped_capital_inr"]))
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "discontinue_slow",
            "summary": {
                "products_evaluated": len(items),
                "slowest_products": [item["sku"] for item in items[:limit]],
                "trapped_capital_in_slow_movers_inr": round(
                    sum(item["trapped_capital_inr"] for item in items[:limit]), 2
                ),
                "open_po_obligations_inr": round(sum(item["open_po_value_inr"] for item in items[:limit]), 2),
            },
            "items": items[:limit],
        }

    def _wholesale_order(self, sku: str, quantity: int) -> Dict[str, Any]:
        """Assess whether a large wholesale order can be fulfilled."""
        item = next((row for row in self._items() if row.get("sku") == sku), None)
        if not item:
            return {"error": f"SKU {sku} not found"}
        velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
        lead_time = self.store.to_float(item.get("metrics", {}).get("lead_time_days"), 7.0)
        usable = self.store.to_float(item.get("inventory", {}).get("usable_on_hand"))
        incoming = self.store.to_float(item.get("inventory", {}).get("incoming"))
        committed = self.store.to_float(item.get("inventory", {}).get("reserved"))
        available_after_commitments = max(0.0, usable - committed)
        forecast_demand = velocity * lead_time
        available_for_order = max(0.0, available_after_commitments + incoming - forecast_demand)
        shortfall = max(0.0, quantity - available_for_order)
        supplier = self.store.suppliers().get(sku, {})
        purchase_cost = shortfall * self.store.to_float(item.get("unit_cost"))
        margin = quantity * self.store.to_float(item.get("unit_gross_margin"))
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "wholesale_order",
            "sku": sku,
            "product_name": item.get("product_name"),
            "order_quantity": quantity,
            "summary": {
                "usable_units": int(usable),
                "committed_units": int(committed),
                "incoming_units": int(incoming),
                "forecast_demand_during_lead_time": int(forecast_demand),
                "available_for_order": int(available_for_order),
                "shortfall_units": int(shortfall),
                "feasible_now": shortfall <= 0,
                "replenishment_purchase_cost_inr": round(purchase_cost, 2),
                "gross_margin_if_fulfilled_inr": round(margin, 2),
            },
            "supplier": {
                "supplier_id": supplier.get("supplier_id"),
                "lead_time_days": int(self.store.to_float(supplier.get("lead_time_days"), lead_time)),
                "moq": self.store.to_int(supplier.get("minimum_order_qty")),
            },
            "recommendation": (
                "Commit the order and schedule replenishment for the shortfall."
                if shortfall > 0
                else "The order can be fulfilled from available and incoming stock."
            ),
        }

    def _seasonal_plan(self, horizon_days: int, uplift_percent: float) -> Dict[str, Any]:
        """Build a demand and inventory plan for a seasonal event."""
        items = []
        total_purchase_cost = 0.0
        for item in self._items():
            sku = item.get("sku", "")
            velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
            net_position = self.store.to_float(item.get("inventory", {}).get("net_position"))
            unit_cost = self.store.to_float(item.get("unit_cost"))
            seasonal_demand = velocity * horizon_days * (1.0 + uplift_percent / 100.0)
            gap = max(0.0, seasonal_demand - net_position)
            purchase_units = math.ceil(gap)
            purchase_cost = purchase_units * unit_cost
            total_purchase_cost += purchase_cost
            items.append(
                {
                    "sku": sku,
                    "product_name": item.get("product_name"),
                    "baseline_daily_velocity": round(velocity, 2),
                    "seasonal_demand_units": int(seasonal_demand),
                    "net_position_units": int(net_position),
                    "gap_units": int(gap),
                    "purchase_units": purchase_units,
                    "purchase_cost_inr": round(purchase_cost, 2),
                }
            )
        items.sort(key=lambda item: item["purchase_cost_inr"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "seasonal_plan",
            "horizon_days": horizon_days,
            "uplift_percent": uplift_percent,
            "assumptions": ["Seasonal uplift is applied uniformly.", "Supplier lead times are unchanged."],
            "summary": {
                "total_purchase_cost_inr": round(total_purchase_cost, 2),
                "skus_needing_stock": sum(1 for item in items if item["purchase_units"] > 0),
            },
            "items": items,
        }

    def _service_level_simulation(self, reduction_percent: float) -> Dict[str, Any]:
        """Delegate a financial service-level question to the finance engine."""
        from coded_tools.inventory_ops.inventory_finance_tool import InventoryFinanceTool

        result = InventoryFinanceTool().invoke(
            {"action": "service_level_simulation", "reduction_percent": reduction_percent}, {}
        )
        if isinstance(result, dict):
            result["scenario"] = "service_level_simulation"
        return result

    def _service_vs_cost(self) -> Dict[str, Any]:
        """Compare service-level targets against inventory investment."""
        targets = [1.28, 1.65, 2.33]
        scenarios = []
        for z_value in targets:
            total_safety_value = 0.0
            total_demand_value = 0.0
            for item in self._items():
                sku = item.get("sku", "")
                velocity, std_dev = self._daily_stats(sku)
                lead_time = self.store.to_float(item.get("metrics", {}).get("lead_time_days"), 7.0)
                unit_cost = self.store.to_float(item.get("unit_cost"))
                selling_price = self.store.to_float(item.get("selling_price"))
                safety_stock = z_value * std_dev * math.sqrt(lead_time)
                total_safety_value += safety_stock * unit_cost
                total_demand_value += velocity * selling_price
            scenarios.append(
                {
                    "service_level_z": z_value,
                    "target_cycle_service_level_pct": round(0.5 * (1.0 + math.erf(z_value / math.sqrt(2.0))) * 100, 1),
                    "safety_stock_investment_inr": round(total_safety_value, 2),
                    "daily_demand_value_inr": round(total_demand_value, 2),
                }
            )
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "service_vs_cost",
            "assumptions": [
                "Safety stock uses recent demand variability and supplier lead time.",
                "Higher service targets increase inventory investment.",
            ],
            "scenarios": scenarios,
            "recommendation": (
                "Choose the lowest-cost target that protects critical SKUs; "
                "the marginal investment rises faster than the service gain."
            ),
        }

    def _supplier_failure(self, supplier_id: str) -> Dict[str, Any]:
        """Estimate exposure and contingency options if a supplier fails."""
        suppliers = self.store.load("suppliers.csv")
        affected = [row for row in suppliers if row.get("supplier_id") == supplier_id]
        if not affected:
            return {"error": f"Supplier {supplier_id} not found"}
        alternates = self.store.load("supplier_alternatives.csv")
        items = []
        for supplier in affected:
            sku = supplier.get("sku", "")
            item = next((row for row in self._items() if row.get("sku") == sku), None)
            if not item:
                continue
            velocity = self.store.to_float(item.get("metrics", {}).get("daily_sales_velocity"))
            usable = self.store.to_float(item.get("inventory", {}).get("usable_on_hand"))
            days_to_shortage = usable / velocity if velocity > 0 else 999.0
            alt = [row for row in alternates if row.get("sku") == sku]
            alt.sort(key=lambda row: self.store.to_float(row.get("lead_time_days")))
            best_alt = alt[0] if alt else None
            items.append(
                {
                    "sku": sku,
                    "product_name": item.get("product_name"),
                    "supplier_id": supplier_id,
                    "daily_velocity": round(velocity, 2),
                    "usable_units": int(usable),
                    "days_to_shortage": round(days_to_shortage, 1),
                    "alternate_supplier": best_alt.get("supplier_id") if best_alt else None,
                    "alternate_lead_time_days": (
                        int(self.store.to_float(best_alt.get("lead_time_days"))) if best_alt else None
                    ),
                    "alternate_unit_cost": (self.store.to_float(best_alt.get("unit_cost")) if best_alt else None),
                    "cost_delta_per_unit": (
                        round(
                            self.store.to_float(best_alt.get("unit_cost"))
                            - self.store.to_float(supplier.get("unit_cost")),
                            2,
                        )
                        if best_alt
                        else None
                    ),
                    "contingency_rank": 1 if best_alt else 2,
                }
            )
        items.sort(key=lambda item: item["days_to_shortage"])
        return {
            "as_of": self.store.reference_time().isoformat(),
            "scenario": "supplier_failure",
            "failed_supplier_id": supplier_id,
            "summary": {
                "affected_skus": len(items),
                "first_shortage_sku": items[0]["sku"] if items else None,
                "days_to_first_shortage": items[0]["days_to_shortage"] if items else None,
                "skus_with_alternate": sum(1 for item in items if item["alternate_supplier"]),
            },
            "items": items,
            "recommendation": (
                "Qualify the alternate supplier immediately and expedite the first order."
                if any(item["days_to_shortage"] < 14 for item in items)
                else "Monitor and prepare the alternate supplier as a contingency."
            ),
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Execute a planning scenario action."""
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = self._normalize_action(args.get("action", "demand_surge"))
        sku = self.store.normalize_optional(args.get("sku"))
        supplier_id = self.store.normalize_optional(args.get("supplier_id")) or "SUP-003"
        quantity = self.store.to_int(args.get("quantity"), 100)
        horizon = self.store.to_int(args.get("horizon_days"), 30)
        limit = self.store.to_int(args.get("limit"), 2)
        demand_change = self.store.to_float(args.get("demand_change_percent"), 20.0)
        lead_multiplier = self.store.to_float(args.get("lead_time_multiplier"), 2.0)
        budget_change = self.store.to_float(args.get("budget_change_percent"), -30.0)
        budget = self.store.to_float(args.get("budget_limit"), 30000.0)
        uplift = self.store.to_float(args.get("uplift_percent"), 20.0)
        reduction = self.store.to_float(args.get("reduction_percent"), 10.0)

        if action == "demand_surge":
            return self._demand_surge(demand_change, horizon)
        if action == "lead_time_double":
            return self._lead_time_double(lead_multiplier, horizon)
        if action == "budget_cut":
            return self._budget_cut(budget_change, budget)
        if action == "discontinue_slow":
            return self._discontinue_slow(limit)
        if action == "wholesale_order":
            if not sku:
                return {"error": "wholesale_order requires a sku"}
            return self._wholesale_order(sku, quantity)
        if action == "seasonal_plan":
            return self._seasonal_plan(horizon, uplift)
        if action == "service_vs_cost":
            return self._service_vs_cost()
        if action == "service_level_simulation":
            return self._service_level_simulation(reduction)
        if action == "supplier_failure":
            return self._supplier_failure(supplier_id)
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
