"""
Replenishment planning CodedTool for Neuro-SAN.
Builds a prioritized queue, forecast-based quantities, order-by dates, excess and
dead-stock views, budget optimization, order-frequency comparisons, and automation policy.
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
from coded_tools.inventory_ops.demand_forecast_tool import DemandForecastTool
from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool
from coded_tools.inventory_ops.inventory_reconciliation_tool import InventoryReconciliationTool
from coded_tools.inventory_ops.supplier_constraints_tool import SupplierConstraintsTool

logger = logging.getLogger(__name__)


class ReplenishmentEngineTool(CodedTool):
    """
    CodedTool that turns forecasts and policies into constrained replenishment decisions.
    """

    RISK_WEIGHTS = {"CRITICAL": 3.0, "WARNING": 2.0, "HEALTHY": 1.0, "OVERSTOCKED": 0.1}

    def __init__(self) -> None:
        """Initialize the tool and its deterministic dependencies."""
        self.store = InventoryDataStore()
        self.analytics_tool = InventoryAnalyticsTool()
        self.forecast_tool = DemandForecastTool()
        self.supplier_tool = SupplierConstraintsTool()
        self.reconciliation_tool = InventoryReconciliationTool()
        logger.info("%s initialized.", self.__class__.__name__)

    def _policy(self, sku: str) -> Dict[str, str]:
        """Return the replenishment policy for a SKU's ABC class."""
        abc = self._abc_map().get(sku, "C")
        policies = {row.get("abc_class"): row for row in self.store.load("replenishment_policy.csv")}
        return policies.get(
            abc,
            {
                "abc_class": "C",
                "service_level_z": "1.04",
                "review_period_days": "30",
                "excess_dos": "90",
                "automation_limit_inr": "5000",
            },
        )

    def _abc_map(self) -> Dict[str, str]:
        """Classify SKUs by cumulative revenue without importing the agent-facing tool."""
        rows = [row for row in self.store.sales() if row.get("status") == "fulfilled"]
        revenue: Dict[str, float] = {}
        for row in rows:
            sku = row.get("sku", "")
            revenue[sku] = revenue.get(sku, 0.0) + (
                self.store.to_float(row.get("quantity")) * self.store.to_float(row.get("unit_selling_price"))
            )
        total = sum(revenue.values())
        if total <= 0:
            return {}
        result = {}
        cumulative = 0.0
        for sku, value in sorted(revenue.items(), key=lambda item: item[1], reverse=True):
            cumulative += value
            share = cumulative / total
            result[sku] = "A" if share <= 0.8 else "B" if share <= 0.95 else "C"
        return result

    def _demand_stats(self, sku: str, window: int = 28) -> Tuple[float, float]:
        """Return mean and standard deviation of daily demand for the recent window."""
        rows = [row for row in self.store.sales() if row.get("sku") == sku and row.get("status") == "fulfilled"]
        quantities: Dict[datetime.date, float] = {}
        for row in rows:
            parsed = self.store.to_datetime(row.get("order_date"))
            if parsed:
                day = parsed.date()
                quantities[day] = quantities.get(day, 0.0) + self.store.to_float(row.get("quantity"))
        if not quantities:
            return 0.0, 0.0
        latest = max(quantities)
        start = latest - datetime.timedelta(days=window - 1)
        values = [quantities.get(start + datetime.timedelta(days=offset), 0.0) for offset in range(window)]
        mean = sum(values) / window
        variance = sum((value - mean) ** 2 for value in values) / max(1, window - 1)
        return mean, math.sqrt(variance)

    def _open_po_quantity(self, sku: str) -> int:
        """Return the open purchase-order quantity for a SKU."""
        total = 0.0
        for row in self.store.open_purchase_orders():
            if row.get("sku") != sku:
                continue
            requested = self.store.to_float(row.get("requested_qty"))
            confirmed = self.store.to_float(row.get("confirmed_qty"))
            total += confirmed if confirmed > 0 else requested
        return int(total)

    def _supplier(self, sku: str) -> Dict[str, str]:
        """Return the primary supplier row for a SKU."""
        return self.store.suppliers().get(sku, {})

    def _position(self, sku: str) -> Dict[str, Any]:
        """Return the current inventory position and risk for a SKU."""
        analysis = self.analytics_tool.invoke({"sku": sku}, {}).get("sku_analysis", {})
        return {
            "net_position": int(analysis.get("inventory", {}).get("net_position", 0)),
            "available_to_sell": int(analysis.get("inventory", {}).get("available_to_sell", 0)),
            "incoming": int(analysis.get("inventory", {}).get("incoming", 0)),
            "risk": analysis.get("risk_assessment", {}).get("level", "HEALTHY"),
            "selling_price": self.store.to_float(analysis.get("selling_price")),
            "unit_margin": self.store.to_float(analysis.get("unit_gross_margin")),
            "unit_cost": self.store.to_float(analysis.get("unit_cost")),
        }

    def _quantity(self, sku: str) -> Dict[str, Any]:
        """Calculate a forecast-based, supplier-compliant order quantity."""
        policy = self._policy(sku)
        supplier = self._supplier(sku)
        lead_time = self.store.to_float(supplier.get("lead_time_days"), 7.0)
        lead_time_sigma = self.store.to_float(supplier.get("lead_time_std_dev"), 2.0)
        review_period = self.store.to_float(policy.get("review_period_days"), 14.0)
        service_z = self.store.to_float(policy.get("service_level_z"), 1.28)
        mean, std = self._demand_stats(sku)
        forecast = self.forecast_tool.invoke(
            {"action": "forecast", "sku": sku, "horizon_days": int(math.ceil(lead_time + review_period))}, {}
        )
        daily_average = self.store.to_float(forecast.get("daily_average"), mean)
        mean = max(mean, daily_average)
        safety_stock = math.ceil(
            service_z * math.sqrt(max(lead_time * (std**2) + (mean**2) * (lead_time_sigma**2), 0.0))
        )
        target = mean * (lead_time + review_period) + safety_stock
        position = self._position(sku)
        open_po = self._open_po_quantity(sku)
        raw_quantity = max(0, math.ceil(target - position["net_position"] - open_po))
        if raw_quantity <= 0:
            return {
                "sku": sku,
                "policy_class": policy.get("abc_class"),
                "service_level_z": service_z,
                "lead_time_days": int(lead_time),
                "lead_time_sigma_days": lead_time_sigma,
                "review_period_days": int(review_period),
                "forecast_daily_average": round(daily_average, 2),
                "demand_std_dev": round(std, 2),
                "safety_stock_units": safety_stock,
                "target_stock_units": int(math.ceil(target)),
                "net_position": position["net_position"],
                "open_po_units": open_po,
                "raw_order_quantity": 0,
                "final_order_quantity": 0,
                "supplier_id": supplier.get("supplier_id"),
                "supplier_name": supplier.get("supplier_name"),
                "unit_cost": position["unit_cost"],
                "line_cost_inr": 0.0,
                "adjustment_reason": "No order required.",
                "explanation": "Current position and open purchase orders already cover the policy target.",
            }
        rounded = self.supplier_tool.invoke({"sku": sku, "recommended_units": raw_quantity}, {})
        line = rounded.get("order_lines", [{}])[0] if rounded.get("order_lines") else {}
        final_quantity = self.store.to_int(line.get("final_batch_qty"), raw_quantity)
        return {
            "sku": sku,
            "policy_class": policy.get("abc_class"),
            "service_level_z": service_z,
            "lead_time_days": int(lead_time),
            "lead_time_sigma_days": lead_time_sigma,
            "review_period_days": int(review_period),
            "forecast_daily_average": round(daily_average, 2),
            "demand_std_dev": round(std, 2),
            "safety_stock_units": safety_stock,
            "target_stock_units": int(math.ceil(target)),
            "net_position": position["net_position"],
            "open_po_units": open_po,
            "raw_order_quantity": raw_quantity,
            "final_order_quantity": final_quantity,
            "supplier_id": line.get("supplier_id") or supplier.get("supplier_id"),
            "supplier_name": line.get("supplier_name") or supplier.get("supplier_name"),
            "unit_cost": self.store.to_float(line.get("unit_cost"), position["unit_cost"]),
            "line_cost_inr": self.store.to_float(line.get("total_line_cost")),
            "adjustment_reason": line.get("adjustment_reason", "No supplier adjustment required."),
            "explanation": (
                f"Target {int(math.ceil(target))} units = {round(daily_average, 2)}/day x "
                f"{int(lead_time + review_period)} days + {safety_stock} safety stock; "
                f"subtract position {position['net_position']} and open POs {open_po}."
            ),
        }

    def _queue(self, limit: int = 10) -> Dict[str, Any]:
        """Build a prioritized replenishment queue."""
        items = []
        for sku in self.store.products():
            quantity = self._quantity(sku)
            position = self._position(sku)
            if quantity["final_order_quantity"] <= 0:
                continue
            weight = self.RISK_WEIGHTS.get(position["risk"], 1.0)
            priority = quantity["final_order_quantity"] * position["unit_margin"] * weight
            items.append(
                {
                    "sku": sku,
                    "product_name": self.store.products().get(sku, {}).get("product_name"),
                    "risk_level": position["risk"],
                    "order_quantity": quantity["final_order_quantity"],
                    "line_cost_inr": quantity["line_cost_inr"],
                    "priority_score": round(priority, 2),
                    "reason": quantity["explanation"],
                    "supplier_id": quantity["supplier_id"],
                    "forecast_confidence": self.forecast_tool.invoke(
                        {"action": "forecast", "sku": sku, "horizon_days": 30}, {}
                    ).get("confidence"),
                }
            )
        items.sort(key=lambda item: item["priority_score"], reverse=True)
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "items_to_reorder": len(items),
                "total_estimated_cost_inr": round(sum(item["line_cost_inr"] for item in items), 2),
            },
            "items": items[:limit],
        }

    def _order_by_date(self, sku: str) -> Dict[str, Any]:
        """Estimate the latest safe order date and stockout probability."""
        position = self._position(sku)
        supplier = self._supplier(sku)
        lead_time = self.store.to_float(supplier.get("lead_time_days"), 7.0)
        lead_sigma = max(self.store.to_float(supplier.get("lead_time_std_dev"), 2.0), 0.1)
        mean, _ = self._demand_stats(sku)
        daily_average = max(
            mean,
            self.forecast_tool.invoke({"action": "forecast", "sku": sku, "horizon_days": 30}, {}).get(
                "daily_average", mean
            ),
        )
        runout_days = position["net_position"] / daily_average if daily_average > 0 else 999.0
        order_by_days = runout_days - lead_time
        order_by = self.store.reference_time() + datetime.timedelta(days=order_by_days)
        z_score = (runout_days - lead_time) / lead_sigma
        stockout_probability = 0.5 * (1.0 - math.erf(z_score / math.sqrt(2.0)))
        return {
            "sku": sku,
            "net_position": position["net_position"],
            "daily_average": round(daily_average, 2),
            "projected_runout_date": (self.store.reference_time() + datetime.timedelta(days=runout_days))
            .date()
            .isoformat(),
            "lead_time_days": int(lead_time),
            "latest_safe_order_date": order_by.date().isoformat(),
            "days_until_order_deadline": round(order_by_days, 1),
            "stockout_probability_before_delivery": round(max(0.0, min(1.0, stockout_probability)), 3),
            "recommendation": "ORDER_NOW" if order_by_days <= 1 else "ORDER_SOON" if order_by_days <= 7 else "MONITOR",
        }

    def _excess(self) -> Dict[str, Any]:
        """Identify excess stock and the capital tied up in it."""
        items = []
        for sku, product in self.store.products().items():
            policy = self._policy(sku)
            mean, _ = self._demand_stats(sku)
            position = self._position(sku)
            excess_dos = self.store.to_float(policy.get("excess_dos"), 60.0)
            target = mean * excess_dos
            excess_units = max(0, position["available_to_sell"] - target)
            if excess_units <= 0:
                continue
            items.append(
                {
                    "sku": sku,
                    "product_name": product.get("product_name"),
                    "on_hand": position["available_to_sell"],
                    "daily_average": round(mean, 2),
                    "policy_excess_days": int(excess_dos),
                    "excess_units": int(excess_units),
                    "capital_tied_up_inr": round(excess_units * position["unit_cost"], 2),
                }
            )
        items.sort(key=lambda item: item["capital_tied_up_inr"], reverse=True)
        return {
            "summary": {
                "excess_skus": len(items),
                "total_capital_tied_up_inr": round(sum(item["capital_tied_up_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _dead_stock(self) -> Dict[str, Any]:
        """Identify persistent low-demand inventory at risk of becoming dead stock."""
        items = []
        reference = self.store.reference_time().date()
        for sku, product in self.store.products().items():
            mean, _ = self._demand_stats(sku, window=60)
            position = self._position(sku)
            if position["available_to_sell"] <= 0:
                continue
            sales_rows = [
                row for row in self.store.sales() if row.get("sku") == sku and row.get("status") == "fulfilled"
            ]
            last_sale = max(
                (
                    self.store.to_datetime(row.get("order_date")).date()
                    for row in sales_rows
                    if self.store.to_datetime(row.get("order_date"))
                ),
                default=None,
            )
            days_since_sale = (reference - last_sale).days if last_sale else 999
            dos = position["available_to_sell"] / mean if mean > 0 else 999.0
            if mean <= 0.25 and dos > 60:
                risk = "DEAD_STOCK"
            elif mean <= 0.5 and dos > 60:
                risk = "AT_RISK"
            else:
                continue
            items.append(
                {
                    "sku": sku,
                    "product_name": product.get("product_name"),
                    "available_to_sell": position["available_to_sell"],
                    "daily_average_60d": round(mean, 2),
                    "days_of_supply": round(dos, 1),
                    "days_since_last_sale": days_since_sale,
                    "risk": risk,
                    "trapped_capital_inr": round(position["available_to_sell"] * position["unit_cost"], 2),
                    "recommendation": "Clearance or transfer" if risk == "DEAD_STOCK" else "Review assortment",
                }
            )
        items.sort(key=lambda item: item["trapped_capital_inr"], reverse=True)
        return {
            "summary": {
                "dead_or_at_risk_skus": len(items),
                "total_trapped_capital_inr": round(sum(item["trapped_capital_inr"] for item in items), 2),
            },
            "items": items,
        }

    def _reduce_order(self, sku: str, order_quantity: int, horizon: int = 30) -> Dict[str, Any]:
        """Show the service and financial trade-offs of reducing a purchase order."""
        if order_quantity <= 0:
            quantity = self._quantity(sku)
            order_quantity = quantity["final_order_quantity"]
        position = self._position(sku)
        forecast = self.forecast_tool.invoke({"action": "forecast", "sku": sku, "horizon_days": horizon}, {})
        demand = self.store.to_float(forecast.get("daily_average")) * horizon
        options = []
        for reduction in (0, 10, 20, 30, 50):
            reduced = int(math.floor(order_quantity * (1.0 - reduction / 100.0)))
            supply = position["net_position"] + reduced
            fill_rate = min(1.0, supply / demand) if demand > 0 else 1.0
            stockout_units = max(0.0, demand - supply)
            options.append(
                {
                    "reduction_percent": reduction,
                    "order_quantity": reduced,
                    "capital_saved_inr": round((order_quantity - reduced) * position["unit_cost"], 2),
                    "expected_fill_rate": round(fill_rate, 3),
                    "expected_stockout_units": int(math.ceil(stockout_units)),
                    "expected_lost_margin_inr": round(stockout_units * position["unit_margin"], 2),
                }
            )
        return {
            "sku": sku,
            "current_order_quantity": order_quantity,
            "forecast_horizon_days": horizon,
            "forecast_demand_units": int(math.ceil(demand)),
            "options": options,
        }

    def _budget_allocate(self, budget_limit: float) -> Dict[str, Any]:
        """Solve a bounded knapsack over discrete case-pack options."""
        candidates = []
        for sku in self.store.products():
            quantity = self._quantity(sku)
            if quantity["final_order_quantity"] <= 0:
                continue
            supplier = self._supplier(sku)
            case_pack = max(1, self.store.to_int(supplier.get("case_pack_qty"), 1))
            unit_cost = quantity["unit_cost"]
            position = self._position(sku)
            mean, _ = self._demand_stats(sku)
            demand = max(mean * 30, 1.0)
            max_packs = max(1, math.ceil(demand / case_pack) + 2)
            options = []
            for packs in range(0, max_packs + 1):
                units = packs * case_pack
                cost = round(units * unit_cost, 2)
                value = min(units, demand) * position["unit_margin"] * self.RISK_WEIGHTS.get(position["risk"], 1.0)
                options.append({"packs": packs, "units": units, "cost": cost, "value": value})
            candidates.append(
                {
                    "sku": sku,
                    "risk": position["risk"],
                    "case_pack": case_pack,
                    "options": options,
                }
            )
        scale = 100
        capacity = int(budget_limit // scale)
        dp = [0.0] * (capacity + 1)
        choices: List[List[int]] = []
        for candidate in candidates:
            option_choice = [-1] * (capacity + 1)
            best_options = candidate["options"]
            for budget_unit in range(capacity, -1, -1):
                best_value = dp[budget_unit]
                best_index = -1
                for index, option in enumerate(best_options):
                    cost_units = int(math.ceil(option["cost"] / scale))
                    if cost_units <= budget_unit:
                        value = dp[budget_unit - cost_units] + option["value"]
                        if value > best_value:
                            best_value = value
                            best_index = index
                dp[budget_unit] = best_value
                option_choice[budget_unit] = best_index
            choices.append(option_choice)
        allocated = []
        remaining = capacity
        for index in range(len(candidates) - 1, -1, -1):
            option_index = choices[index][remaining]
            if option_index is None or option_index < 0:
                continue
            option = candidates[index]["options"][option_index]
            if option["units"] > 0:
                allocated.append(
                    {
                        "sku": candidates[index]["sku"],
                        "risk_level": candidates[index]["risk"],
                        "units": option["units"],
                        "packs": option["packs"],
                        "cost_inr": option["cost"],
                        "protected_margin_inr": round(option["value"], 2),
                    }
                )
                remaining -= int(math.ceil(option["cost"] / scale))
        allocated.reverse()
        spent = sum(item["cost_inr"] for item in allocated)
        return {
            "budget_limit_inr": budget_limit,
            "budget_spent_inr": round(spent, 2),
            "remaining_budget_inr": round(budget_limit - spent, 2),
            "strategy": "bounded_knapsack_dp",
            "allocated": allocated,
            "protected_margin_inr": round(sum(item["protected_margin_inr"] for item in allocated), 2),
        }

    def _order_frequency(self, sku: str) -> Dict[str, Any]:
        """Compare one large order with several smaller orders including cost trade-offs."""
        policy = self._policy(sku)
        cost_policy = {row.get("policy_id"): row for row in self.store.load("cost_policy.csv")}.get("DEFAULT", {})
        ordering_cost = self.store.to_float(cost_policy.get("ordering_cost_inr"), 750.0)
        freight = self.store.to_float(cost_policy.get("freight_per_order_inr"), 500.0)
        holding_rate = self.store.to_float(cost_policy.get("holding_rate_pct"), 22.0) / 100.0
        penalty = self.store.to_float(cost_policy.get("stockout_penalty_pct"), 35.0) / 100.0
        mean, _ = self._demand_stats(sku)
        monthly_demand = max(mean * 30, 1.0)
        position = self._position(sku)
        options = []
        for orders in (1, 2, 4, 6):
            quantity = monthly_demand / orders
            average_inventory = quantity / 2
            holding_cost = average_inventory * position["unit_cost"] * holding_rate
            order_cost = orders * (ordering_cost + freight)
            coverage = (position["net_position"] + monthly_demand) / monthly_demand
            stockout_probability = max(0.0, 1.0 - min(1.0, coverage))
            shortage_cost = stockout_probability * monthly_demand * position["unit_margin"] * penalty
            options.append(
                {
                    "orders_per_month": orders,
                    "units_per_order": int(math.ceil(quantity)),
                    "ordering_and_freight_inr": round(order_cost, 2),
                    "holding_cost_inr": round(holding_cost, 2),
                    "expected_shortage_cost_inr": round(shortage_cost, 2),
                    "total_cost_inr": round(order_cost + holding_cost + shortage_cost, 2),
                }
            )
        best = min(options, key=lambda option: option["total_cost_inr"])
        return {"sku": sku, "policy_class": policy.get("abc_class"), "options": options, "recommended": best}

    def _moq_alternatives(self, sku: str) -> Dict[str, Any]:
        """Quantify forced overbuy from MOQ and compare approved alternatives."""
        quantity = self._quantity(sku)
        requested = quantity["raw_order_quantity"]
        primary = self.supplier_tool.invoke({"sku": sku, "recommended_units": max(requested, 1)}, {})
        primary_line = primary.get("order_lines", [{}])[0]
        primary_units = self.store.to_int(primary_line.get("final_batch_qty"), requested)
        unit_cost = self.store.to_float(primary_line.get("unit_cost"))
        alternatives = []
        for row in self.store.load("supplier_alternatives.csv"):
            if row.get("sku") != sku:
                continue
            alt_moq = self.store.to_int(row.get("minimum_order_qty"), 1)
            alt_pack = max(1, self.store.to_int(row.get("case_pack_qty"), 1))
            alt_units = max(alt_moq, math.ceil(max(requested, 1) / alt_pack) * alt_pack)
            alternatives.append(
                {
                    "supplier_id": row.get("supplier_id"),
                    "supplier_name": row.get("supplier_name"),
                    "lead_time_days": self.store.to_int(row.get("lead_time_days")),
                    "unit_cost": self.store.to_float(row.get("unit_cost")),
                    "order_units": alt_units,
                    "order_cost_inr": round(alt_units * self.store.to_float(row.get("unit_cost")), 2),
                    "overbuy_units": max(0, alt_units - requested),
                }
            )
        alternatives.sort(key=lambda item: item["order_cost_inr"])
        return {
            "sku": sku,
            "requested_units": requested,
            "primary_supplier": {
                "supplier_id": primary_line.get("supplier_id"),
                "order_units": primary_units,
                "overbuy_units": max(0, primary_units - requested),
                "overbuy_cost_inr": round(max(0, primary_units - requested) * unit_cost, 2),
                "line_cost_inr": self.store.to_float(primary_line.get("total_line_cost")),
            },
            "alternatives": alternatives,
            "recommendation": (
                "Compare the lowest-cost compliant alternative with the primary supplier."
                if alternatives
                else "No approved alternative supplier."
            ),
        }

    def _automation_policy(self) -> Dict[str, Any]:
        """Classify recommendations as automatable, review-required, or blocked."""
        data_quality = self.reconciliation_tool.invoke({"action": "data_quality"}, {})
        reliability = self.forecast_tool.invoke({"action": "reliability"}, {})
        reliability_map = {item["sku"]: item for item in reliability.get("items", [])}
        items = []
        for sku in self.store.products():
            quantity = self._quantity(sku)
            if quantity["final_order_quantity"] <= 0:
                continue
            position = self._position(sku)
            policy = self._policy(sku)
            limit = self.store.to_float(policy.get("automation_limit_inr"), 5000.0)
            confidence = reliability_map.get(sku, {}).get("confidence", "LOW")
            sku_high_risk = any(
                issue.get("severity") == "HIGH" and issue.get("sku") == sku for issue in data_quality.get("issues", [])
            )
            global_high_risk = any(
                issue.get("severity") == "HIGH" and not issue.get("sku") for issue in data_quality.get("issues", [])
            )
            reasons = []
            verdict = "AUTO_APPROVE"
            if sku_high_risk:
                verdict = "BLOCK"
                reasons.append("High-severity SKU data quality issue")
            elif global_high_risk:
                verdict = "HUMAN_REVIEW"
                reasons.append("Source freshness issue requires review")
            elif confidence in ("LOW", "INSUFFICIENT_HISTORY"):
                verdict = "HUMAN_REVIEW"
                reasons.append(f"Forecast confidence is {confidence}")
            elif quantity["line_cost_inr"] > limit:
                verdict = "HUMAN_REVIEW"
                reasons.append(f"Exposure exceeds automation limit of {limit}")
            elif position["risk"] == "CRITICAL":
                verdict = "HUMAN_REVIEW"
                reasons.append("Critical stockout risk requires approval")
            items.append(
                {
                    "sku": sku,
                    "order_quantity": quantity["final_order_quantity"],
                    "line_cost_inr": quantity["line_cost_inr"],
                    "risk_level": position["risk"],
                    "forecast_confidence": confidence,
                    "verdict": verdict,
                    "reasons": reasons,
                }
            )
        return {
            "summary": {
                "auto_approve": sum(1 for item in items if item["verdict"] == "AUTO_APPROVE"),
                "human_review": sum(1 for item in items if item["verdict"] == "HUMAN_REVIEW"),
                "blocked": sum(1 for item in items if item["verdict"] == "BLOCK"),
            },
            "items": items,
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute a replenishment planning action.

        :param args: Dictionary with "action" and optional SKU, quantity, budget, or horizon.
        :param sly_data: Unused context dictionary.
        :return: Structured replenishment result.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = args.get("action", "queue")
        sku = self.store.normalize_optional(args.get("sku"))
        if action == "queue":
            return self._queue(self.store.to_int(args.get("limit"), 10))
        if action == "quantity":
            if not sku:
                return {"error": "quantity requires a sku"}
            return self._quantity(sku)
        if action == "order_by_date":
            if not sku:
                return {"error": "order_by_date requires a sku"}
            return self._order_by_date(sku)
        if action == "excess":
            return self._excess()
        if action == "dead_stock":
            return self._dead_stock()
        if action == "reduce_order":
            if not sku:
                return {"error": "reduce_order requires a sku"}
            return self._reduce_order(
                sku, self.store.to_int(args.get("order_quantity"), 0), self.store.to_int(args.get("horizon_days"), 30)
            )
        if action == "budget_allocate":
            return self._budget_allocate(self.store.to_float(args.get("budget_limit"), 50000.0))
        if action == "order_frequency":
            if not sku:
                return {"error": "order_frequency requires a sku"}
            return self._order_frequency(sku)
        if action == "moq_alternatives":
            if not sku:
                return {"error": "moq_alternatives requires a sku"}
            return self._moq_alternatives(sku)
        if action == "automation_policy":
            return self._automation_policy()
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
