"""
ABC/XYZ Portfolio Classification CodedTool for Neuro-SAN.
Classifies SKUs using the standard ABC (revenue contribution) and XYZ (demand predictability)
dual-axis segmentation used by every professional supply chain operations team.

ABC: A = top 80% cumulative revenue, B = next 15%, C = bottom 5%.
XYZ: X = CV < 0.5 (stable), Y = 0.5 <= CV < 1.0 (variable), Z = CV >= 1.0 (erratic).
"""

import asyncio
import csv
import logging
import math
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger(__name__)


class ABCClassificationTool(CodedTool):
    """
    CodedTool that performs ABC/XYZ portfolio segmentation on the product catalog.
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
        Execute ABC/XYZ classification.

        :param args: Dictionary containing optional "abc_a_threshold" (default 0.80),
                     "abc_b_threshold" (default 0.95).
        :param sly_data: Unused context dictionary.
        :return: Classified SKU portfolio with strategic recommendations.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        abc_a = float(args.get("abc_a_threshold") or 0.80)
        abc_b = float(args.get("abc_b_threshold") or 0.95)

        products = {p.get("sku"): p for p in self._load_csv("products.csv")}
        sales_rows = self._load_csv("sales.csv")

        # Compute revenue per SKU
        sku_revenue: Dict[str, float] = {}
        sku_daily_qty: Dict[str, Dict[str, float]] = {}

        for s in sales_rows:
            if s.get("status") != "fulfilled":
                continue
            sku = s.get("sku")
            qty = float(s.get("quantity", 0))
            price = float(s.get("unit_selling_price", 0))
            rev = qty * price
            sku_revenue[sku] = sku_revenue.get(sku, 0.0) + rev

            dt = s.get("order_date", "")[:10]
            if sku not in sku_daily_qty:
                sku_daily_qty[sku] = {}
            sku_daily_qty[sku][dt] = sku_daily_qty[sku].get(dt, 0.0) + qty

        total_revenue = sum(sku_revenue.values())
        if total_revenue == 0:
            return {"error": "No fulfilled sales data found for ABC classification."}

        # Sort descending by revenue
        sorted_skus = sorted(sku_revenue.items(), key=lambda x: x[1], reverse=True)

        classified = []
        cumulative_rev = 0.0

        for sku, rev in sorted_skus:
            cumulative_rev += rev
            cumulative_pct = cumulative_rev / total_revenue

            # ABC classification
            if cumulative_pct <= abc_a:
                abc_class = "A"
            elif cumulative_pct <= abc_b:
                abc_class = "B"
            else:
                abc_class = "C"

            # XYZ classification (coefficient of variation of daily demand)
            daily_vals = list(sku_daily_qty.get(sku, {}).values())
            if len(daily_vals) >= 2:
                mean_d = sum(daily_vals) / len(daily_vals)
                variance = sum((x - mean_d) ** 2 for x in daily_vals) / (len(daily_vals) - 1)
                std_d = math.sqrt(variance)
                cv = std_d / mean_d if mean_d > 0 else 999.0
            else:
                cv = 999.0  # Insufficient data = erratic

            if cv < 0.5:
                xyz_class = "X"
            elif cv < 1.0:
                xyz_class = "Y"
            else:
                xyz_class = "Z"

            segment = f"{abc_class}{xyz_class}"

            # Strategic recommendation per segment
            strategies = {
                "AX": (
                    "High-value, predictable demand. Automate reordering with tight safety stock. "
                    "Prime candidate for Just-In-Time (JIT)."
                ),
                "AY": "High-value, variable demand. Use statistical forecasting with moderate safety stock buffer.",
                "AZ": (
                    "High-value, erratic demand. Requires demand sensing and higher safety stock. "
                    "Investigate root cause of volatility."
                ),
                "BX": "Mid-value, predictable. Standard reorder point with periodic review.",
                "BY": "Mid-value, variable. Monthly review cycle with safety stock adjustment.",
                "BZ": "Mid-value, erratic. Consider make-to-order or consignment stocking.",
                "CX": "Low-value, predictable. Bulk purchase with long review cycles to minimize ordering cost.",
                "CY": "Low-value, variable. Minimal safety stock, accept occasional stockout.",
                "CZ": "Low-value, erratic. Consider discontinuation or dropship model.",
            }

            prod = products.get(sku, {})
            classified.append(
                {
                    "sku": sku,
                    "product_name": prod.get("product_name", sku),
                    "category": prod.get("category"),
                    "period_revenue_inr": round(rev, 2),
                    "revenue_contribution_pct": round((rev / total_revenue) * 100, 1),
                    "cumulative_revenue_pct": round(cumulative_pct * 100, 1),
                    "abc_class": abc_class,
                    "demand_cv": round(cv, 3),
                    "xyz_class": xyz_class,
                    "segment": segment,
                    "strategic_recommendation": strategies.get(segment, "Review manually."),
                }
            )

        # Portfolio summary
        a_count = sum(1 for c in classified if c["abc_class"] == "A")
        b_count = sum(1 for c in classified if c["abc_class"] == "B")
        c_count = sum(1 for c in classified if c["abc_class"] == "C")
        a_rev = sum(c["period_revenue_inr"] for c in classified if c["abc_class"] == "A")

        return {
            "portfolio_summary": {
                "total_skus_classified": len(classified),
                "total_period_revenue_inr": round(total_revenue, 2),
                "class_A_count": a_count,
                "class_A_revenue_pct": round((a_rev / total_revenue) * 100, 1) if total_revenue > 0 else 0,
                "class_B_count": b_count,
                "class_C_count": c_count,
            },
            "classified_skus": classified,
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
