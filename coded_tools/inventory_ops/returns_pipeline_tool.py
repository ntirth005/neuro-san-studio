"""
Return & RTO (Return to Origin) Pipeline CodedTool for Neuro-SAN.
Analyzes e-commerce return rates, COD rejection rates, resellable inventory in transit,
and calculates True Net Sales Velocity to prevent over-ordering.
"""

import asyncio
import csv
import datetime
import logging
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger(__name__)


class ReturnPipelineTool(CodedTool):
    """
    CodedTool that audits reverse logistics, RTO rates, and adjusts net inventory velocity.
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
        Execute returns and reverse logistics pipeline analysis.

        :param args: Dictionary containing optional "sku" (str) to filter by SKU.
        :param sly_data: Unused context dictionary.
        :return: Return metrics, true net velocity, and in-transit return restock projections.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        target_sku = args.get("sku")
        if target_sku is not None and str(target_sku).strip().lower() in {"", "null", "none", "all", "any"}:
            target_sku = None

        products = {p.get("sku"): p for p in self._load_csv("products.csv")}
        sales_rows = self._load_csv("sales.csv")
        return_rows = self._load_csv("returns.csv")

        # Group sales by SKU and keep a recent window for velocity.
        sku_gross_sales: Dict[str, float] = {}
        sku_recent_sales: Dict[str, float] = {}
        sale_dates = []
        for s in sales_rows:
            if s.get("status") != "fulfilled":
                continue
            sku = s.get("sku")
            quantity = float(s.get("quantity", 0))
            sku_gross_sales[sku] = sku_gross_sales.get(sku, 0.0) + quantity
            parsed = datetime.datetime.fromisoformat(s.get("order_date", "").replace("Z", "+00:00"))
            sale_dates.append(parsed)
        latest_sale = max(sale_dates).date() if sale_dates else datetime.date.today()
        window_start = latest_sale - datetime.timedelta(days=27)
        for s in sales_rows:
            if s.get("status") != "fulfilled":
                continue
            parsed = datetime.datetime.fromisoformat(s.get("order_date", "").replace("Z", "+00:00"))
            if parsed.date() >= window_start:
                sku = s.get("sku")
                sku_recent_sales[sku] = sku_recent_sales.get(sku, 0.0) + float(s.get("quantity", 0))

        # Group returns by SKU
        sku_returns: Dict[str, List[Dict[str, str]]] = {}
        for r in return_rows:
            sku = r.get("sku")
            if sku not in sku_returns:
                sku_returns[sku] = []
            sku_returns[sku].append(r)

        skus_to_evaluate = [target_sku] if target_sku else list(products.keys())
        sku_reports = []

        total_returns_count = 0
        total_in_transit_units = 0
        total_scrap_units = 0

        for sku in skus_to_evaluate:
            prod = products.get(sku)
            if not prod:
                continue

            gross_units = sku_gross_sales.get(sku, 0.0)
            recent_units = sku_recent_sales.get(sku, 0.0)
            returns_list = sku_returns.get(sku, [])

            total_returned_qty = sum(float(r.get("quantity", 0)) for r in returns_list)
            resellable_qty = sum(
                float(r.get("quantity", 0)) for r in returns_list if r.get("condition") == "resellable"
            )
            damaged_qty = sum(float(r.get("quantity", 0)) for r in returns_list if r.get("condition") == "damaged")
            in_transit_qty = sum(
                float(r.get("quantity", 0)) for r in returns_list if r.get("condition") == "in_transit"
            )

            total_returns_count += int(total_returned_qty)
            total_in_transit_units += int(in_transit_qty)
            total_scrap_units += int(damaged_qty)

            return_rate_pct = round((total_returned_qty / gross_units) * 100, 1) if gross_units > 0 else 0.0

            # 28-day observation window for velocity.
            obs_days = 28.0
            gross_velocity = round(recent_units / obs_days, 2)
            # Net consumed velocity accounts for resellable returns re-entering stock.
            effective_units_consumed = max(0.0, recent_units - resellable_qty - in_transit_qty)
            true_net_velocity = round(effective_units_consumed / obs_days, 2)

            # Working capital saved by accounting for incoming RTO returns
            unit_cost = float(prod.get("unit_cost", 0))
            capital_saved_by_rto_inr = round(in_transit_qty * unit_cost, 2)

            sku_reports.append(
                {
                    "sku": sku,
                    "product_name": prod.get("product_name"),
                    "gross_units_sold": int(gross_units),
                    "gross_daily_velocity": gross_velocity,
                    "total_returned_units": int(total_returned_qty),
                    "return_rate_pct": return_rate_pct,
                    "return_breakdown": {
                        "resellable_returned_units": int(resellable_qty),
                        "damaged_scrap_units": int(damaged_qty),
                        "in_transit_rto_units": int(in_transit_qty),
                    },
                    "velocity_adjustment": {
                        "true_net_daily_velocity": true_net_velocity,
                        "velocity_reduction_pct": round(
                            ((gross_velocity - true_net_velocity) / max(gross_velocity, 0.01)) * 100, 1
                        ),
                    },
                    "procurement_impact": {
                        "incoming_rto_stock_offset": int(in_transit_qty),
                        "capital_saved_by_offset_inr": capital_saved_by_rto_inr,
                        "recommendation": (
                            f"Deduct {int(in_transit_qty)} units from reorder PO since they are in transit "
                            f"saving ₹{capital_saved_by_rto_inr:,.2f} in working capital."
                            if in_transit_qty > 0
                            else "No RTO stock currently in transit."
                        ),
                    },
                }
            )

        return {
            "reverse_logistics_summary": {
                "total_returns_logged": total_returns_count,
                "in_transit_rto_units_pipeline": total_in_transit_units,
                "damaged_scrap_units": total_scrap_units,
                "insight": (
                    "Accounting for RTO pipeline avoids over-procurement and accounts for returns "
                    "re-entering available stock."
                ),
            },
            "sku_return_analytics": sku_reports if not target_sku else (sku_reports[0] if sku_reports else {}),
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
