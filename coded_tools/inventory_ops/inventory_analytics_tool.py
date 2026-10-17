"""
Inventory Analytics CodedTool for Neuro-SAN.
Computes deterministic stock positions, daily sales velocities, lead-time demand,
safety stock, and reorder points based on ingested store data.
"""

import asyncio
import datetime
import logging
import math
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore

logger = logging.getLogger(__name__)


class InventoryAnalyticsTool(CodedTool):
    """
    CodedTool that calculates inventory positions, sales velocities, and stockout risks.
    """

    def __init__(self) -> None:
        """Initialize the InventoryAnalyticsTool with the shared data store."""
        self.store = InventoryDataStore()
        self.data_dir = self.store.data_dir
        logger.info("InventoryAnalyticsTool initialized with data_dir: %s", self.data_dir)

    def _load_csv(self, filename: str) -> List[Dict[str, str]]:
        """Load records from a CSV file."""
        return self.store.load(filename)

    def _compute_sku_metrics(self, sku: str) -> Optional[Dict[str, Any]]:
        """Compute comprehensive inventory and sales metrics for a single SKU."""
        products = self.store.products()
        inventory_by_sku = self.store.inventory_by_sku()
        sales_rows = self._load_csv("sales.csv")
        suppliers = self.store.suppliers()

        prod = products.get(sku)
        if not prod:
            return None

        location_rows = inventory_by_sku.get(sku, [])
        sup = suppliers.get(sku, {})

        # 1. Inventory Position, aggregated across locations
        on_hand = sum(self.store.to_float(row.get("on_hand_qty")) for row in location_rows)
        reserved = sum(self.store.to_float(row.get("reserved_qty")) for row in location_rows)
        incoming = sum(self.store.to_float(row.get("incoming_qty")) for row in location_rows)
        unavailable = sum(self.store.to_float(row.get("unavailable_qty")) for row in location_rows)

        available_to_sell = max(0.0, on_hand - reserved - unavailable)
        net_position = max(0.0, available_to_sell + incoming)
        usable_on_hand = available_to_sell

        # 2. Sales Velocity with Outlier Filtering & Uncensored Demand Correction
        # Parse daily sales
        sku_orders = [s for s in sales_rows if s.get("sku") == sku and s.get("status") == "fulfilled"]

        daily_quantities: Dict[str, float] = {}
        for s in sku_orders:
            # Group by day YYYY-MM-DD
            dt = s.get("order_date", "")[:10]
            qty = float(s.get("quantity", 0))
            daily_quantities[dt] = daily_quantities.get(dt, 0.0) + qty

        # Use the most recent 28 calendar days for a stable velocity estimate.
        observation_days = 28.0
        if daily_quantities:
            latest_date = max(datetime.date.fromisoformat(day) for day in daily_quantities)
            window_start = (latest_date - datetime.timedelta(days=27)).isoformat()
            daily_quantities = {day: quantity for day, quantity in daily_quantities.items() if day >= window_start}
        daily_values = list(daily_quantities.values())

        # Outlier Detection & Winsorization (handle sudden 1-day B2B bulk spikes)
        outlier_flags = []
        if daily_values:
            sorted_vals = sorted(daily_values)
            median_val = sorted_vals[len(sorted_vals) // 2]
            outlier_threshold = max(3.0 * median_val, 10.0)

            clean_daily_values = []
            for dt, val in daily_quantities.items():
                if val > outlier_threshold:
                    outlier_flags.append({"date": dt, "raw_units": val, "capped_units": outlier_threshold})
                    clean_daily_values.append(outlier_threshold)
                else:
                    clean_daily_values.append(val)
            total_clean_units = sum(clean_daily_values)
        else:
            total_clean_units = 0.0
            clean_daily_values = []

        # Uncensored Demand Correction:
        # If usable on-hand was zero or critically stockout, adjust active selling days
        active_selling_days = observation_days
        if net_position <= 0 and len(daily_quantities) < observation_days:
            # If stockout, exclude zero-sales days where item was physically unavailable
            active_selling_days = max(float(len(daily_quantities)), 1.0)

        daily_velocity = total_clean_units / active_selling_days if active_selling_days > 0 else 0.0

        # Calculate standard deviation of daily sales for safety stock
        if len(clean_daily_values) > 1:
            mean = total_clean_units / len(clean_daily_values)
            variance = sum((x - mean) ** 2 for x in clean_daily_values) / (len(clean_daily_values) - 1)
            std_dev_demand = math.sqrt(variance)
        else:
            std_dev_demand = 0.5 * max(daily_velocity, 0.5)

        # 3. Lead Time & Safety Stock
        lead_time_days = float(sup.get("lead_time_days", 7))
        lead_time_demand = daily_velocity * lead_time_days

        # Service level factor Z = 1.65 (approx 95% cycle service level)
        service_factor_z = 1.65
        safety_stock = math.ceil(service_factor_z * std_dev_demand * math.sqrt(lead_time_days))
        reorder_point = math.ceil(lead_time_demand + safety_stock)

        # 4. Stockout Risk Assessment
        days_of_supply = round(net_position / daily_velocity, 1) if daily_velocity > 0 else 999.0

        if days_of_supply < (lead_time_days * 0.75):
            risk_level = "CRITICAL"
            risk_reason = (
                f"Days of supply ({days_of_supply}d) is strictly less than supplier lead time "
                f"({lead_time_days}d). High probability of stockout before new shipment arrives."
            )
        elif days_of_supply <= lead_time_days + (safety_stock / max(daily_velocity, 0.1)):
            risk_level = "WARNING"
            risk_reason = (
                f"Inventory position is below or near Reorder Point ({reorder_point} units). Reorder recommended soon."
            )
        elif days_of_supply > 60:
            risk_level = "OVERSTOCKED"
            risk_reason = f"Excess inventory ({days_of_supply} days of supply). Working capital tied up."
        else:
            risk_level = "HEALTHY"
            risk_reason = f"Adequate stock buffer ({days_of_supply} days of supply) covering lead time."

        raw_reorder_qty = max(0, reorder_point - int(net_position))

        # 5. Financial & Capital Efficiency Metrics
        unit_cost = float(prod.get("unit_cost", 0))
        selling_price = float(prod.get("selling_price", 0))
        unit_gross_margin = round(selling_price - unit_cost, 2)
        gross_margin_pct = round((unit_gross_margin / selling_price) * 100, 1) if selling_price > 0 else 0.0
        daily_profit_contrib = round(daily_velocity * unit_gross_margin, 2)
        gmroi_index = round((daily_profit_contrib / max(unit_cost, 1.0)) * 100, 2)  # Return per rupee invested
        trapped_capital = round(usable_on_hand * unit_cost, 2)

        return {
            "sku": sku,
            "product_name": prod.get("product_name"),
            "category": prod.get("category"),
            "unit_cost": unit_cost,
            "selling_price": selling_price,
            "unit_gross_margin": unit_gross_margin,
            "gross_margin_pct": gross_margin_pct,
            "currency": prod.get("currency", "INR"),
            "inventory": {
                "on_hand": int(on_hand),
                "reserved": int(reserved),
                "incoming": int(incoming),
                "unavailable": int(unavailable),
                "available_to_sell": int(available_to_sell),
                "usable_on_hand": int(usable_on_hand),
                "net_position": int(net_position),
                "trapped_capital_inr": trapped_capital,
                "locations": [
                    {
                        "location_id": row.get("location_id"),
                        "on_hand": int(self.store.to_float(row.get("on_hand_qty"))),
                        "reserved": int(self.store.to_float(row.get("reserved_qty"))),
                        "incoming": int(self.store.to_float(row.get("incoming_qty"))),
                        "unavailable": int(self.store.to_float(row.get("unavailable_qty"))),
                    }
                    for row in location_rows
                ],
            },
            "metrics": {
                "daily_sales_velocity": round(daily_velocity, 2),
                "lead_time_days": int(lead_time_days),
                "lead_time_demand": round(lead_time_demand, 1),
                "safety_stock": int(safety_stock),
                "reorder_point": int(reorder_point),
                "days_of_supply": days_of_supply,
                "suggested_raw_reorder_qty": raw_reorder_qty,
                "active_selling_days": active_selling_days,
                "outliers_adjusted": outlier_flags,
            },
            "financial_efficiency": {
                "daily_gross_profit_contribution": daily_profit_contrib,
                "gmroi_capital_index": gmroi_index,
            },
            "risk_assessment": {
                "level": risk_level,
                "reason": risk_reason,
            },
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute inventory analytics calculations.

        :param args: Dictionary containing:
            - "sku": (Optional) specific SKU string.
            - "action": (Optional) "audit_all" or "audit_risks".
        :param sly_data: Context information (unused here).
        :return: Structured analytics summary.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        target_sku = self.store.normalize_optional(args.get("sku"))
        action = args.get("action", "audit_all")

        products = self._load_csv("products.csv")
        all_skus = [p.get("sku") for p in products if p.get("sku")]

        if target_sku:
            metrics = self._compute_sku_metrics(target_sku)
            if not metrics:
                return {"error": f"SKU {target_sku} not found in product catalog"}
            return {"sku_analysis": metrics}

        # Otherwise audit all SKUs
        results = []
        critical_count = 0
        warning_count = 0

        for sku in all_skus:
            res = self._compute_sku_metrics(sku)
            if res:
                level = res.get("risk_assessment", {}).get("level")
                if level == "CRITICAL":
                    critical_count += 1
                elif level == "WARNING":
                    warning_count += 1

                if action == "audit_risks" and level not in ["CRITICAL", "WARNING"]:
                    continue
                results.append(res)

        return {
            "summary": {
                "total_skus_evaluated": len(all_skus),
                "critical_stockouts": critical_count,
                "warnings": warning_count,
            },
            "items": results,
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
