"""
Supplier Reliability & Dual-Sourcing Scoring CodedTool for Neuro-SAN.
Computes dynamic safety stock incorporating lead-time variance (sigma_LT), scores vendor
fulfillment reliability, evaluates volume discount tiers, and flags dual-sourcing triggers.
"""

import asyncio
import csv
import datetime
import logging
import math
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger(__name__)


class SupplierReliabilityTool(CodedTool):
    """
    CodedTool that audits vendor fulfillment risk, lead-time jitter, and volume discounts.
    """

    def __init__(self) -> None:
        """Initialize with data file paths."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.abspath(os.path.join(base_dir, "..", "..", "data")),
            os.path.abspath(os.path.join(base_dir, "..", "..", "..", "data")),
            os.path.abspath(os.path.join(os.getcwd(), "data")),
        ]
        self.data_dir = next((d for d in candidates if os.path.isdir(d)), candidates[0])
        logger.info("%s initialized with data_dir: %s", self.__class__.__name__, self.data_dir)

    def _load_csv(self, filename: str) -> List[Dict[str, str]]:
        """Load records from a CSV file."""
        file_path = os.path.join(self.data_dir, filename)
        if not os.path.exists(file_path):
            logger.warning("Data file not found: %s", file_path)
            return []
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [dict(row) for row in reader]

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute supplier reliability scoring and stochastic safety stock calculation.

        :param args: Dictionary containing optional "sku" or "supplier_id".
        :param sly_data: Unused context dictionary.
        :return: Vendor reliability scorecards, stochastic safety stocks, and dual-sourcing alerts.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        target_sku = args.get("sku")
        target_supplier = args.get("supplier_id")

        suppliers = self._load_csv("suppliers.csv")
        products = {p.get("sku"): p for p in self._load_csv("products.csv")}
        sales_rows = self._load_csv("sales.csv")

        # Estimate daily demand mean and variance from the most recent 28 days.
        sku_daily_map: Dict[str, Dict[datetime.date, float]] = {}
        for s in sales_rows:
            if s.get("status") != "fulfilled":
                continue
            parsed = datetime.datetime.fromisoformat(s.get("order_date", "").replace("Z", "+00:00"))
            sku = s.get("sku")
            day = parsed.date()
            sku_daily_map.setdefault(sku, {})
            sku_daily_map[sku][day] = sku_daily_map[sku].get(day, 0.0) + float(s.get("quantity", 0))
        sku_sales_map: Dict[str, List[float]] = {}
        for sku, daily in sku_daily_map.items():
            latest = max(daily)
            start = latest - datetime.timedelta(days=27)
            sku_sales_map[sku] = [daily.get(start + datetime.timedelta(days=offset), 0.0) for offset in range(28)]

        scorecards = []
        high_risk_vendors = 0

        for sup in suppliers:
            sku = sup.get("sku")
            supplier_id = sup.get("supplier_id")

            if target_sku and sku != target_sku:
                continue
            if target_supplier and supplier_id != target_supplier:
                continue

            lead_time = float(sup.get("lead_time_days", 7))
            lt_std_dev = float(sup.get("lead_time_std_dev", 2.0))
            on_time_pct = float(sup.get("on_time_delivery_pct", 90.0))
            reject_pct = float(sup.get("quality_reject_pct", 1.0))
            vol_tier_discount = float(sup.get("volume_tier_discount_pct", 5.0))
            vol_tier_threshold = int(sup.get("volume_tier_threshold", 50))
            unit_cost = float(sup.get("unit_cost", 100.0))

            # Demand statistics
            sales_list = sku_sales_map.get(sku, [0.0] * 28)
            mean_demand = sum(sales_list) / len(sales_list)
            if len(sales_list) > 1:
                mean_sales = sum(sales_list) / len(sales_list)
                variance = sum((value - mean_sales) ** 2 for value in sales_list) / (len(sales_list) - 1)
                std_demand = math.sqrt(variance)
            else:
                std_demand = max(0.5 * mean_demand, 0.5)

            # Stochastic Safety Stock formula with Lead Time Variance:
            # SS = Z * sqrt( LT * sigma_D^2 + D^2 * sigma_LT^2 )
            # Z = 1.65 (95% service level)
            z_factor = 1.65
            stochastic_variance = (lead_time * (std_demand**2)) + ((mean_demand**2) * (lt_std_dev**2))
            stochastic_safety_stock = math.ceil(z_factor * math.sqrt(max(stochastic_variance, 0.1)))
            naive_safety_stock = math.ceil(z_factor * std_demand * math.sqrt(lead_time))
            safety_stock_deficit_units = max(0, stochastic_safety_stock - naive_safety_stock)

            # Composite Vendor Reliability Score (0 - 100)
            reliability_score = round(
                (on_time_pct * 0.55)
                + (max(0.0, 100.0 - (reject_pct * 10.0)) * 0.25)
                + (max(0.0, 100.0 - (lt_std_dev * 10.0)) * 0.20),
                1,
            )

            if reliability_score >= 88.0:
                grade = "TIER-1 (EXCELLENT)"
                dual_sourcing_needed = False
                risk_summary = "High fulfillment consistency. Vendor qualifies for automated replenishment."
            elif reliability_score >= 80.0:
                grade = "TIER-2 (ACCEPTABLE)"
                dual_sourcing_needed = False
                risk_summary = "Moderate delivery variance. Maintain safety stock buffer."
            else:
                grade = "HIGH_RISK (VULNERABLE)"
                dual_sourcing_needed = True
                high_risk_vendors += 1
                risk_summary = (
                    f"Frequent delivery jitter (std dev ±{lt_std_dev}d) and lower on-time rate ({on_time_pct}%). "
                    f"Immediate dual-sourcing or secondary backup vendor strongly recommended."
                )

            # Volume discount savings evaluation
            discounted_unit_cost = round(unit_cost * (1.0 - (vol_tier_discount / 100.0)), 2)
            tier_savings_per_unit = round(unit_cost - discounted_unit_cost, 2)
            total_tier_discount_savings_inr = round(tier_savings_per_unit * vol_tier_threshold, 2)

            scorecards.append(
                {
                    "supplier_id": supplier_id,
                    "supplier_name": sup.get("supplier_name"),
                    "sku": sku,
                    "product_name": products.get(sku, {}).get("product_name", sku),
                    "fulfillment_performance": {
                        "lead_time_days": int(lead_time),
                        "lead_time_std_dev_days": lt_std_dev,
                        "on_time_delivery_pct": on_time_pct,
                        "quality_reject_pct": reject_pct,
                        "reliability_score_out_of_100": reliability_score,
                        "vendor_grade": grade,
                    },
                    "stochastic_safety_stock": {
                        "stochastic_safety_stock_units": stochastic_safety_stock,
                        "naive_static_safety_stock_units": naive_safety_stock,
                        "safety_buffer_adjustment_units": safety_stock_deficit_units,
                        "formula_note": (
                            "Incorporates supplier delivery variance (sigma_LT) to prevent surprise stockouts."
                        ),
                    },
                    "volume_tier_opportunity": {
                        "tier_threshold_units": vol_tier_threshold,
                        "discount_pct": vol_tier_discount,
                        "base_unit_cost_inr": unit_cost,
                        "discounted_unit_cost_inr": discounted_unit_cost,
                        "potential_procurement_savings_inr": total_tier_discount_savings_inr,
                    },
                    "sourcing_strategy": {
                        "dual_sourcing_required": dual_sourcing_needed,
                        "strategic_guidance": risk_summary,
                    },
                }
            )

        return {
            "supplier_audit_summary": {
                "total_vendors_evaluated": len(scorecards),
                "high_risk_vendors_flagged": high_risk_vendors,
                "stochastic_buffer_status": "Active stochastic safety stock model applied across all catalog SKUs.",
            },
            "vendor_scorecards": scorecards if not (target_sku or target_supplier) else scorecards[0],
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
