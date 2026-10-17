"""
Dead Stock Liquidation CodedTool for Neuro-SAN.
Identifies trapped working capital in overstocked or slow-moving inventory (> 60 Days of Supply),
and calculates markdown clearance strategies and cash-unlock projections to re-fund procurement.
"""

import asyncio
import logging
import math
from typing import Any
from typing import Dict
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool

logger = logging.getLogger(__name__)


class DeadStockLiquidationTool(CodedTool):
    """
    CodedTool that identifies overstocked SKUs and calculates capital unlock strategies.
    """

    def __init__(self) -> None:
        """Initialize with analytics tool dependency."""
        self.analytics_tool = InventoryAnalyticsTool()
        logger.info("%s initialized.", self.__class__.__name__)

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute dead stock auditing and liquidation strategy formulation.

        :param args: Dictionary containing:
            - "dos_threshold": (float, optional) Days of supply threshold to classify as excess (default 60).
            - "target_healthy_dos": (float, optional) Target days of supply to normalize down to (default 30).
            - "discount_percent": (float, optional) Markdown discount percent to model (default 20.0).
        :param sly_data: Unused context dictionary.
        :return: Audit of trapped capital and clearance monetization options.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        dos_threshold = float(args.get("dos_threshold", 60.0))
        target_healthy_dos = float(args.get("target_healthy_dos", 30.0))
        discount_pct = float(args.get("discount_percent", 20.0))

        audit_res = self.analytics_tool.invoke({"action": "audit_all"}, sly_data)
        if isinstance(audit_res, str) or "items" not in audit_res:
            return {"error": "Failed to retrieve inventory metrics for liquidation audit."}

        items = audit_res.get("items", [])
        dead_stock_items = []
        total_trapped_capital = 0.0
        total_potential_cash_unlock = 0.0

        for it in items:
            dos = it.get("metrics", {}).get("days_of_supply", 0.0)
            velocity = it.get("metrics", {}).get("daily_sales_velocity", 0.0)
            on_hand = it.get("inventory", {}).get("usable_on_hand", 0)
            unit_cost = it.get("unit_cost", 0.0)
            selling_price = it.get("selling_price", 0.0)

            if dos > dos_threshold and on_hand > 0:
                trapped_cash = on_hand * unit_cost
                total_trapped_capital += trapped_cash

                # Calculate excess units beyond healthy buffer
                healthy_stock_target = math.ceil(max(velocity, 0.2) * target_healthy_dos)
                excess_units = max(0, on_hand - healthy_stock_target)

                # Clearance pricing modeling
                discounted_price = round(selling_price * (1.0 - (discount_pct / 100.0)), 2)
                discounted_unit_margin = round(discounted_price - unit_cost, 2)
                # Assume standard retail price elasticity = 2.2x velocity lift
                projected_lifted_velocity = round(max(velocity, 0.2) * 2.2, 2)
                if projected_lifted_velocity > 0:
                    projected_days_to_clear = math.ceil(excess_units / projected_lifted_velocity)
                else:
                    projected_days_to_clear = 999

                gross_revenue_unlocked = round(excess_units * discounted_price, 2)
                total_potential_cash_unlock += gross_revenue_unlocked

                dead_stock_items.append(
                    {
                        "sku": it.get("sku"),
                        "product_name": it.get("product_name"),
                        "category": it.get("category"),
                        "current_on_hand": on_hand,
                        "days_of_supply": dos,
                        "unit_cost": unit_cost,
                        "selling_price": selling_price,
                        "trapped_working_capital_inr": round(trapped_cash, 2),
                        "excess_units_above_target": excess_units,
                        "clearance_strategy": {
                            "proposed_discount_pct": discount_pct,
                            "clearance_selling_price": discounted_price,
                            "retained_unit_margin": discounted_unit_margin,
                            "projected_daily_velocity": projected_lifted_velocity,
                            "projected_days_to_clear": projected_days_to_clear,
                            "cash_capital_unlocked_inr": gross_revenue_unlocked,
                        },
                        "bundling_recommendation": (
                            f"Bundle {it.get('sku')} with high-velocity SKU-001 or SKU-002 "
                            f"at 15% bundle discount to accelerate inventory clearance."
                        ),
                    }
                )

        return {
            "dead_stock_audit_summary": {
                "overstocked_skus_found": len(dead_stock_items),
                "total_trapped_capital_inr": round(total_trapped_capital, 2),
                "total_projected_cash_unlock_inr": round(total_potential_cash_unlock, 2),
                "recommended_action": (
                    f"Initiate clearance campaign on {len(dead_stock_items)} SKU(s) to unlock up to "
                    f"₹{total_potential_cash_unlock:,.2f} in liquid capital to fund urgent procurement."
                ),
            },
            "overstocked_items": dead_stock_items,
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
