"""
Supplier sourcing CodedTool for Neuro-SAN.
Scores supplier reliability from purchase/receipt history and evaluates shortfalls,
delays, price changes, alternates, and supplier failure scenarios.
"""

import asyncio
import datetime
import logging
import math
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore
from coded_tools.inventory_ops.demand_forecast_tool import DemandForecastTool
from coded_tools.inventory_ops.inventory_analytics_tool import InventoryAnalyticsTool

logger = logging.getLogger(__name__)


class SupplierSourcingTool(CodedTool):
    """
    CodedTool that answers supplier performance and sourcing-exception questions.
    """

    def __init__(self) -> None:
        """Initialize the tool with deterministic dependencies."""
        self.store = InventoryDataStore()
        self.forecast_tool = DemandForecastTool()
        self.analytics_tool = InventoryAnalyticsTool()
        logger.info("%s initialized.", self.__class__.__name__)

    def _po_rows(self, supplier_id: Optional[str] = None) -> List[Dict[str, str]]:
        """Return purchase orders, optionally filtered by supplier."""
        rows = self.store.load("purchase_orders.csv")
        if supplier_id:
            rows = [row for row in rows if row.get("supplier_id") == supplier_id]
        return rows

    def _receipts(self, po_id: str) -> List[Dict[str, str]]:
        """Return receipts for a purchase order."""
        return [row for row in self.store.load("receipts.csv") if row.get("po_id") == po_id]

    def _events(self, po_id: str) -> List[Dict[str, str]]:
        """Return events for a purchase order."""
        rows = [row for row in self.store.load("po_events.csv") if row.get("po_id") == po_id]
        rows.sort(key=lambda row: row.get("occurred_at", ""))
        return rows

    def _scorecard(self, supplier_id: Optional[str] = None) -> Dict[str, Any]:
        """Compute supplier reliability from actual purchase and receipt records."""
        static_suppliers = {row.get("supplier_id"): row for row in self.store.load("suppliers.csv")}
        supplier_ids = [supplier_id] if supplier_id else list(static_suppliers)
        cards = []
        for current_id in supplier_ids:
            rows = self._po_rows(current_id)
            static = static_suppliers.get(current_id, {})
            receipts_count = 0
            on_time = 0
            fill_rates = []
            lead_times = []
            response_hours = []
            for row in rows:
                po_id = row.get("po_id", "")
                events = self._events(po_id)
                sent = self.store.to_datetime(row.get("sent_at"))
                ack_events = [event for event in events if event.get("event_type") == "acknowledged"]
                if sent and ack_events:
                    acknowledged = self.store.to_datetime(ack_events[0].get("occurred_at"))
                    if acknowledged:
                        response_hours.append((acknowledged - sent).total_seconds() / 3600.0)
                confirmed = self.store.to_int(row.get("confirmed_qty")) or self.store.to_int(row.get("requested_qty"))
                receipts = self._receipts(po_id)
                received = sum(self.store.to_int(receipt.get("received_qty")) for receipt in receipts)
                if receipts:
                    receipts_count += 1
                    fill_rates.append(received / confirmed if confirmed > 0 else 0.0)
                    confirmed_date = self.store.to_datetime(row.get("confirmed_date"))
                    receipt_date = self.store.to_datetime(receipts[0].get("received_at"))
                    if confirmed_date and receipt_date and receipt_date.date() <= confirmed_date.date():
                        on_time += 1
                    sent_date = self.store.to_datetime(row.get("sent_at"))
                    if sent_date and receipt_date:
                        lead_times.append((receipt_date - sent_date).total_seconds() / 86400.0)
            otif = (
                (on_time / receipts_count * 100.0)
                if receipts_count
                else self.store.to_float(static.get("on_time_delivery_pct"), 90.0)
            )
            fill_rate = (sum(fill_rates) / len(fill_rates) * 100.0) if fill_rates else 95.0
            if response_hours:
                response_score = max(0.0, 100.0 - (sum(response_hours) / len(response_hours) * 2.0))
            else:
                response_score = 80.0
            score = round(otif * 0.5 + fill_rate * 0.3 + response_score * 0.2, 1)
            if score >= 88:
                grade = "TIER-1"
            elif score >= 78:
                grade = "TIER-2"
            else:
                grade = "HIGH_RISK"
            cards.append(
                {
                    "supplier_id": current_id,
                    "supplier_name": static.get("supplier_name"),
                    "purchase_orders": len(rows),
                    "receipts_observed": receipts_count,
                    "otif_pct": round(otif, 1),
                    "fill_rate_pct": round(fill_rate, 1),
                    "average_response_hours": (
                        round(sum(response_hours) / len(response_hours), 1) if response_hours else None
                    ),
                    "average_lead_time_days": round(sum(lead_times) / len(lead_times), 1) if lead_times else None,
                    "reliability_score": score,
                    "grade": grade,
                    "dual_sourcing_recommended": score < 80,
                }
            )
        cards.sort(key=lambda card: card["reliability_score"])
        return {
            "summary": {
                "suppliers_evaluated": len(cards),
                "high_risk": sum(1 for card in cards if card["grade"] == "HIGH_RISK"),
            },
            "scorecards": cards,
        }

    def _po(self, po_id: str) -> Optional[Dict[str, str]]:
        """Return a purchase order by ID."""
        return next((row for row in self.store.load("purchase_orders.csv") if row.get("po_id") == po_id), None)

    def _best_alternative(self, sku: str, quantity: int) -> Optional[Dict[str, Any]]:
        """Return the lowest landed-cost approved alternative for a SKU."""
        options = []
        for row in self.store.load("supplier_alternatives.csv"):
            if row.get("sku") != sku or row.get("active", "true").lower() != "true":
                continue
            moq = self.store.to_int(row.get("minimum_order_qty"), 1)
            pack = max(1, self.store.to_int(row.get("case_pack_qty"), 1))
            units = max(moq, math.ceil(max(quantity, 1) / pack) * pack)
            unit_cost = self.store.to_float(row.get("unit_cost"))
            options.append(
                {
                    "supplier_id": row.get("supplier_id"),
                    "supplier_name": row.get("supplier_name"),
                    "order_units": units,
                    "unit_cost": unit_cost,
                    "landed_cost_inr": round(units * unit_cost, 2),
                    "lead_time_days": self.store.to_int(row.get("lead_time_days")),
                    "reliability_score": self.store.to_float(row.get("reliability_score")),
                }
            )
        if not options:
            return None
        options.sort(key=lambda option: (option["landed_cost_inr"], option["lead_time_days"]))
        return options[0]

    def _shortfall_options(self, po_id: str, available_qty: int) -> Dict[str, Any]:
        """Evaluate options when a supplier can ship fewer units than requested."""
        row = self._po(po_id)
        if not row:
            return {"error": f"Unknown purchase order: {po_id}"}
        ordered = self.store.to_int(row.get("confirmed_qty")) or self.store.to_int(row.get("requested_qty"))
        shortfall = max(0, ordered - available_qty)
        sku = row.get("sku", "")
        alternative = self._best_alternative(sku, shortfall) if shortfall else None
        position = self.analytics_tool.invoke({"sku": sku}, {}).get("sku_analysis", {})
        daily_average = self.store.to_float(
            self.forecast_tool.invoke({"action": "forecast", "sku": sku, "horizon_days": 14}, {}).get("daily_average")
        )
        net_position = self.store.to_float(position.get("inventory", {}).get("net_position"))
        runout_days = net_position / daily_average if daily_average > 0 else 999.0
        options = [
            {
                "option": "ACCEPT_PARTIAL_AND_BACKORDER",
                "units_now": available_qty,
                "backorder_units": shortfall,
                "additional_cost_inr": 0.0,
                "stockout_risk": "HIGH" if runout_days <= 7 else "MEDIUM",
                "note": f"Projected runout in {round(runout_days, 1)} days without replacement.",
            }
        ]
        if alternative:
            options.append(
                {
                    "option": "SOURCE_SHORTFALL_FROM_ALTERNATE",
                    "supplier_id": alternative["supplier_id"],
                    "units": alternative["order_units"],
                    "additional_cost_inr": alternative["landed_cost_inr"],
                    "lead_time_days": alternative["lead_time_days"],
                    "stockout_risk": "LOW" if alternative["lead_time_days"] <= runout_days else "MEDIUM",
                }
            )
        options.append(
            {
                "option": "REPRIORITIZE_DEMAND",
                "units_now": available_qty,
                "backorder_units": shortfall,
                "additional_cost_inr": 0.0,
                "stockout_risk": "MEDIUM",
                "note": "Allocate available units to highest-margin or highest-risk orders.",
            }
        )
        return {
            "po_id": po_id,
            "sku": sku,
            "ordered_qty": ordered,
            "available_qty": available_qty,
            "shortfall_units": shortfall,
            "projected_runout_days": round(runout_days, 1),
            "options": options,
        }

    def _delay_impact(self, po_id: str, delay_days: int) -> Dict[str, Any]:
        """Recalculate stock risk for a delayed purchase order."""
        row = self._po(po_id)
        if not row:
            return {"error": f"Unknown purchase order: {po_id}"}
        sku = row.get("sku", "")
        reference = self.store.reference_time()
        confirmed_date = self.store.to_datetime(row.get("confirmed_date")) or reference
        new_arrival = confirmed_date + datetime.timedelta(days=delay_days)
        position = self.analytics_tool.invoke({"sku": sku}, {}).get("sku_analysis", {})
        net_position = self.store.to_float(position.get("inventory", {}).get("net_position"))
        horizon = max(delay_days + 14, 30)
        forecast = self.forecast_tool.invoke({"action": "forecast", "sku": sku, "horizon_days": horizon}, {})
        daily_average = self.store.to_float(forecast.get("daily_average"))
        runout_days = net_position / daily_average if daily_average > 0 else 999.0
        runout_date = reference + datetime.timedelta(days=runout_days)
        unmet_units = max(0.0, daily_average * max(0.0, (new_arrival - runout_date).days))
        return {
            "po_id": po_id,
            "sku": sku,
            "supplier_id": row.get("supplier_id"),
            "original_confirmed_date": row.get("confirmed_date"),
            "delay_days": delay_days,
            "new_expected_arrival": new_arrival.date().isoformat(),
            "net_position": int(net_position),
            "forecast_daily_average": round(daily_average, 2),
            "projected_runout_date": runout_date.date().isoformat(),
            "stockout_gap_days": max(0, (new_arrival.date() - runout_date.date()).days),
            "expected_unmet_units": int(math.ceil(unmet_units)),
            "expected_lost_gmv_inr": round(unmet_units * self.store.to_float(position.get("selling_price")), 2),
            "recommendation": "Expedite, split the shipment, or source from an approved alternate.",
        }

    def _original_unit_cost(self, row: Dict[str, str]) -> float:
        """Recover the pre-change unit cost from the PO event trail or supplier master."""
        for event in self._events(row.get("po_id", "")):
            if event.get("event_type") != "price_change":
                continue
            values = re.findall(r"\d+\.\d+", event.get("detail", ""))
            if len(values) >= 2:
                return float(values[0])
        supplier = self.store.suppliers().get(row.get("sku", ""), {})
        return self.store.to_float(supplier.get("unit_cost"), self.store.to_float(row.get("unit_cost")))

    def _price_change_eval(self, po_id: str, new_unit_cost: float) -> Dict[str, Any]:
        """Evaluate whether a revised supplier price is still acceptable."""
        row = self._po(po_id)
        if not row:
            return {"error": f"Unknown purchase order: {po_id}"}
        sku = row.get("sku", "")
        quantity = self.store.to_int(row.get("confirmed_qty")) or self.store.to_int(row.get("requested_qty"))
        old_cost = self._original_unit_cost(row)
        product = self.store.products().get(sku, {})
        selling_price = self.store.to_float(product.get("selling_price"))
        old_margin = selling_price - old_cost
        new_margin = selling_price - new_unit_cost
        delta = new_unit_cost - old_cost
        old_line = old_cost * quantity
        new_line = new_unit_cost * quantity
        alternative = self._best_alternative(sku, quantity)
        recommendation = "ACCEPT"
        if new_margin <= 0:
            recommendation = "REJECT_OR_RENEGOTIATE"
        elif alternative and alternative["unit_cost"] < new_unit_cost:
            recommendation = "CONSIDER_ALTERNATE"
        elif delta / max(old_cost, 1.0) > 0.1:
            recommendation = "NEGOTIATE"
        return {
            "po_id": po_id,
            "sku": sku,
            "quantity": quantity,
            "old_unit_cost": old_cost,
            "new_unit_cost": new_unit_cost,
            "cost_delta_pct": round(delta / max(old_cost, 1.0) * 100.0, 1),
            "old_line_cost_inr": round(old_line, 2),
            "new_line_cost_inr": round(new_line, 2),
            "margin_impact_per_unit_inr": round(new_margin - old_margin, 2),
            "new_unit_margin_inr": round(new_margin, 2),
            "best_alternate": alternative,
            "recommendation": recommendation,
        }

    def _alternate_suppliers(self, sku: str, quantity: int) -> Dict[str, Any]:
        """Rank approved alternates by landed cost, lead time, and reliability."""
        options = []
        for row in self.store.load("supplier_alternatives.csv"):
            if row.get("sku") != sku or row.get("active", "true").lower() != "true":
                continue
            moq = self.store.to_int(row.get("minimum_order_qty"), 1)
            pack = max(1, self.store.to_int(row.get("case_pack_qty"), 1))
            units = max(moq, math.ceil(max(quantity, 1) / pack) * pack)
            unit_cost = self.store.to_float(row.get("unit_cost"))
            options.append(
                {
                    "supplier_id": row.get("supplier_id"),
                    "supplier_name": row.get("supplier_name"),
                    "order_units": units,
                    "unit_cost": unit_cost,
                    "landed_cost_inr": round(units * unit_cost, 2),
                    "lead_time_days": self.store.to_int(row.get("lead_time_days")),
                    "lead_time_std_dev": self.store.to_float(row.get("lead_time_std_dev")),
                    "reliability_score": self.store.to_float(row.get("reliability_score")),
                    "moq": moq,
                    "case_pack": pack,
                }
            )
        options.sort(key=lambda option: (option["landed_cost_inr"], option["lead_time_days"]))
        return {
            "sku": sku,
            "requested_quantity": quantity,
            "alternates": options,
            "recommendation": options[0] if options else None,
        }

    def _supplier_failure_sim(self, supplier_id: str) -> Dict[str, Any]:
        """Recompute risk if a supplier becomes unavailable."""
        affected = []
        for row in self._po_rows(supplier_id):
            if row.get("status", "").upper() in {"RECEIVED", "CANCELLED", "CLOSED"}:
                continue
            sku = row.get("sku", "")
            quantity = self.store.to_int(row.get("confirmed_qty")) or self.store.to_int(row.get("requested_qty"))
            alternative = self._best_alternative(sku, quantity)
            position = self.analytics_tool.invoke({"sku": sku}, {}).get("sku_analysis", {})
            net_position = self.store.to_float(position.get("inventory", {}).get("net_position"))
            forecast = self.forecast_tool.invoke({"action": "forecast", "sku": sku, "horizon_days": 30}, {})
            daily_average = self.store.to_float(forecast.get("daily_average"))
            runout_days = net_position / daily_average if daily_average > 0 else 999.0
            affected.append(
                {
                    "po_id": row.get("po_id"),
                    "sku": sku,
                    "open_quantity": quantity,
                    "projected_runout_days": round(runout_days, 1),
                    "alternate": alternative,
                    "risk": "HIGH" if runout_days <= 14 else "MEDIUM",
                }
            )
        return {
            "supplier_id": supplier_id,
            "affected_purchase_orders": len(affected),
            "items": affected,
            "recommendation": "Pre-qualify alternates and split open quantities across approved suppliers.",
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute a supplier sourcing action.

        :param args: Dictionary with "action" and optional supplier, PO, SKU, quantity, or price.
        :param sly_data: Unused context dictionary.
        :return: Structured sourcing result.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = args.get("action", "supplier_scorecard")
        supplier_id = self.store.normalize_optional(args.get("supplier_id"))
        po_id = self.store.normalize_optional(args.get("po_id"))
        sku = self.store.normalize_optional(args.get("sku"))
        quantity = self.store.to_int(args.get("quantity"), 1)
        if action == "supplier_scorecard":
            return self._scorecard(supplier_id)
        if action == "shortfall_options":
            if not po_id:
                return {"error": "shortfall_options requires a po_id"}
            return self._shortfall_options(po_id, self.store.to_int(args.get("available_qty"), 0))
        if action == "delay_impact":
            if not po_id:
                return {"error": "delay_impact requires a po_id"}
            return self._delay_impact(po_id, self.store.to_int(args.get("delay_days"), 7))
        if action == "price_change_eval":
            if not po_id:
                return {"error": "price_change_eval requires a po_id"}
            return self._price_change_eval(po_id, self.store.to_float(args.get("new_unit_cost")))
        if action == "alternate_suppliers":
            if not sku:
                return {"error": "alternate_suppliers requires a sku"}
            return self._alternate_suppliers(sku, quantity)
        if action == "supplier_failure_sim":
            if not supplier_id:
                return {"error": "supplier_failure_sim requires a supplier_id"}
            return self._supplier_failure_sim(supplier_id)
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
