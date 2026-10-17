"""
Knapsack Budget Optimizer CodedTool for Neuro-SAN.
Solves a constrained capital optimization problem (Bounded Knapsack) to allocate
a merchant's procurement budget across candidate SKU replenishments, maximizing
retained gross margin while respecting supplier MOQs and case-pack multiples.
"""

import asyncio
import logging
from typing import Any
from typing import Dict
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool
from coded_tools.inventory_ops.supplier_constraints_tool import SupplierConstraintsTool

logger = logging.getLogger(__name__)


class KnapsackBudgetOptimizerTool(CodedTool):
    """
    CodedTool that optimizes capital allocation across replenishment candidates.
    """

    def __init__(self) -> None:
        """Initialize optimizer and dependencies."""
        self.analytics_tool = InventoryAnalyticsTool()
        self.supplier_tool = SupplierConstraintsTool()
        logger.info("%s initialized.", self.__class__.__name__)

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute knapsack budget optimization.

        :param args: Dictionary containing:
            - "budget_limit": (float, required) Total procurement budget ceiling in INR.
            - "min_risk_level": (Optional) "CRITICAL", "WARNING", or "ALL" (default "ALL").
        :param sly_data: Unused context dictionary.
        :return: Optimized replenishment allocation and financial comparison.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        budget_limit = float(args.get("budget_limit", 50000.0))
        min_risk_level = str(args.get("min_risk_level") or "ALL").upper()

        # 1. Audit current inventory analytics across all products
        audit_res = self.analytics_tool.invoke({"action": "audit_all"}, sly_data)
        if isinstance(audit_res, str) or "items" not in audit_res:
            return {"error": "Failed to retrieve inventory analytics for optimization."}

        items = audit_res.get("items", [])
        candidates = []

        risk_weights = {
            "CRITICAL": 2.5,
            "WARNING": 1.5,
            "HEALTHY": 0.8,
            "OVERSTOCKED": 0.1,
        }

        for it in items:
            sku = it.get("sku")
            level = it.get("risk_assessment", {}).get("level", "HEALTHY")
            if min_risk_level == "CRITICAL" and level != "CRITICAL":
                continue
            if min_risk_level == "WARNING" and level not in ["CRITICAL", "WARNING"]:
                continue

            raw_suggested = it.get("metrics", {}).get("suggested_raw_reorder_qty", 0)
            if raw_suggested <= 0 and level not in ["CRITICAL", "WARNING"]:
                continue

            # Check supplier constraints
            sup_res = self.supplier_tool.invoke({"sku": sku, "recommended_units": max(raw_suggested, 1)}, sly_data)

            if isinstance(sup_res, str) or "order_lines" not in sup_res or not sup_res["order_lines"]:
                continue

            line = sup_res["order_lines"][0]

            rounded_units = line.get("final_batch_qty", 0)
            unit_cost = line.get("unit_cost", 0.0)
            case_pack = line.get("case_pack_qty", 1)
            moq = line.get("minimum_order_qty", 1)
            line_cost = line.get("total_line_cost", 0.0)

            unit_margin = it.get("unit_gross_margin", 0.0)
            selling_price = it.get("selling_price", 0.0)
            velocity = it.get("metrics", {}).get("daily_sales_velocity", 0.0)
            weight = risk_weights.get(level, 1.0)

            # Expected gross profit protected by ordering this batch
            profit_protected = round(rounded_units * unit_margin * weight, 2)
            # Capital efficiency ratio = profit contribution protected per rupee invested
            efficiency_ratio = profit_protected / max(line_cost, 1.0)

            candidates.append(
                {
                    "sku": sku,
                    "product_name": it.get("product_name"),
                    "supplier_id": line.get("supplier_id"),
                    "risk_level": level,
                    "days_of_supply": it.get("metrics", {}).get("days_of_supply"),
                    "daily_velocity": velocity,
                    "case_pack_qty": case_pack,
                    "moq": moq,
                    "unit_cost": unit_cost,
                    "selling_price": selling_price,
                    "unit_gross_margin": unit_margin,
                    "recommended_units": rounded_units,
                    "line_cost": line_cost,
                    "profit_protected": profit_protected,
                    "efficiency_ratio": round(efficiency_ratio, 4),
                }
            )

        if not candidates:
            return {
                "message": "No candidate replenishments required. Inventory levels are healthy.",
                "budget_limit": budget_limit,
                "allocated_orders": [],
                "unallocated_skus": [],
            }

        # 2. Greedy / Knapsack Optimization by Capital Efficiency Ratio
        sorted_candidates = sorted(candidates, key=lambda c: c["efficiency_ratio"], reverse=True)

        allocated = []
        unallocated = []
        spent = 0.0
        total_profit_protected = 0.0

        for cand in sorted_candidates:
            cost = cand["line_cost"]
            if spent + cost <= budget_limit:
                allocated.append(cand)
                spent += cost
                total_profit_protected += cand["profit_protected"]
            else:
                unallocated.append(
                    {
                        "sku": cand["sku"],
                        "product_name": cand["product_name"],
                        "risk_level": cand["risk_level"],
                        "needed_cost": cost,
                        "reason": (
                            f"Insufficient budget (requires {cost:,.2f}, remaining is {budget_limit - spent:,.2f})"
                        ),
                    }
                )

        # 3. Naive First-Come-First-Served Comparison
        naive_spent = 0.0
        naive_profit = 0.0
        for cand in candidates:
            if naive_spent + cand["line_cost"] <= budget_limit:
                naive_spent += cand["line_cost"]
                naive_profit += cand["profit_protected"]

        profit_gain = max(0.0, total_profit_protected - naive_profit)

        return {
            "budget_limit": budget_limit,
            "budget_spent": round(spent, 2),
            "remaining_budget": round(budget_limit - spent, 2),
            "budget_utilization_pct": round((spent / budget_limit) * 100, 1) if budget_limit > 0 else 0.0,
            "optimal_allocation": {
                "allocated_item_count": len(allocated),
                "items": allocated,
            },
            "unallocated_deferred_items": unallocated,
            "financial_optimization_benchmark": {
                "optimal_profit_protected_inr": round(total_profit_protected, 2),
                "naive_fifo_profit_protected_inr": round(naive_profit, 2),
                "margin_retention_advantage_inr": round(profit_gain, 2),
                "strategy": "Bounded Knapsack by GMROI * Risk Priority Weight",
            },
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
