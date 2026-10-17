"""
Scenario Simulation CodedTool for Neuro-SAN.
Performs deterministic supply chain stress testing and what-if simulation:
evaluating lead-time shocks (delays), demand surges (flash sales), and projecting
stockout timelines, lost GMV, and buffer mitigation requirements.
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


class ScenarioSimulationTool(CodedTool):
    """
    CodedTool that runs supply chain stress simulations and what-if analyses.
    """

    def __init__(self) -> None:
        """Initialize with analytics tool dependency."""
        self.analytics_tool = InventoryAnalyticsTool()
        logger.info("%s initialized.", self.__class__.__name__)

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute scenario stress simulation.

        :param args: Dictionary containing:
            - "lead_time_shock_days": (float, optional) Additional delay days added to
              supplier lead time (default 5.0).
            - "demand_surge_percent": (float, optional) Percentage surge in daily consumer demand (default 0.0).
            - "target_sku": (str, optional) Optional SKU to isolate analysis.
        :param sly_data: Unused context dictionary.
        :return: Simulation results with stockout sequence, lost GMV, and mitigation advice.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        lead_time_shock = float(args.get("lead_time_shock_days", 5.0))
        demand_surge_pct = float(args.get("demand_surge_percent", 0.0))
        target_sku = args.get("target_sku")

        audit_res = self.analytics_tool.invoke({"action": "audit_all"}, sly_data)
        if isinstance(audit_res, str) or "items" not in audit_res:
            return {"error": "Failed to retrieve inventory baseline for scenario simulation."}

        items = audit_res.get("items", [])
        if target_sku:
            items = [it for it in items if it.get("sku") == target_sku]

        surge_factor = 1.0 + (demand_surge_pct / 100.0)
        simulated_results = []
        total_lost_gmv = 0.0
        total_lost_profit = 0.0
        stockout_count = 0

        for it in items:
            sku = it.get("sku")
            net_position = float(it.get("inventory", {}).get("net_position", 0))
            base_velocity = float(it.get("metrics", {}).get("daily_sales_velocity", 0.0))
            base_lead_time = float(it.get("metrics", {}).get("lead_time_days", 7))
            selling_price = float(it.get("selling_price", 0.0))
            unit_margin = float(it.get("unit_gross_margin", 0.0))

            # Apply stress parameters
            stressed_velocity = round(base_velocity * surge_factor, 2)
            stressed_lead_time = base_lead_time + lead_time_shock

            # Days of supply under stressed demand
            stressed_dos = round(net_position / stressed_velocity, 1) if stressed_velocity > 0 else 999.0

            # Does item stock out before new shipment arrives?
            is_stockout = stressed_dos < stressed_lead_time

            if is_stockout:
                stockout_count += 1
                # Days of unmet demand during shipment delay
                stockout_days = max(0.0, stressed_lead_time - stressed_dos)
                unmet_units = math.ceil(stockout_days * stressed_velocity)
                lost_revenue = round(unmet_units * selling_price, 2)
                lost_margin = round(unmet_units * unit_margin, 2)

                total_lost_gmv += lost_revenue
                total_lost_profit += lost_margin
                urgency = "IMMEDIATE_COLLAPSE" if stressed_dos <= 3.0 else "HIGH_EXPOSURE"
            else:
                stockout_days = 0.0
                unmet_units = 0
                lost_revenue = 0.0
                lost_margin = 0.0
                urgency = "RESILIENT"

            # Emergency air-freight / expedited buffer units to avoid stockout
            emergency_buffer_units = unmet_units

            simulated_results.append(
                {
                    "sku": sku,
                    "product_name": it.get("product_name"),
                    "baseline_velocity": base_velocity,
                    "stressed_velocity": stressed_velocity,
                    "baseline_lead_time_days": int(base_lead_time),
                    "stressed_lead_time_days": int(stressed_lead_time),
                    "stressed_days_of_supply": stressed_dos,
                    "stockout_risk": urgency,
                    "unmet_units_during_gap": unmet_units,
                    "projected_lost_revenue_inr": lost_revenue,
                    "projected_lost_gross_profit_inr": lost_margin,
                    "mitigation_buffer_needed": emergency_buffer_units,
                }
            )

        # Sort by days of supply ascending to show exact collapse timeline
        simulated_results.sort(key=lambda x: x["stressed_days_of_supply"])

        return {
            "simulation_scenario_parameters": {
                "lead_time_shock_delay_days": lead_time_shock,
                "demand_surge_percent": demand_surge_pct,
                "total_skus_evaluated": len(simulated_results),
            },
            "impact_summary": {
                "skus_facing_stockout": stockout_count,
                "total_projected_lost_gmv_inr": round(total_lost_gmv, 2),
                "total_projected_lost_profit_inr": round(total_lost_profit, 2),
                "first_to_fail_sku": simulated_results[0]["sku"] if simulated_results else None,
            },
            "sku_stress_timeline": simulated_results,
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
