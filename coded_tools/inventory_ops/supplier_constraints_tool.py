"""
Supplier Constraints CodedTool for Neuro-SAN.
Matches reorder requirements with supplier MOQ, case-pack multiples, wholesale unit costs,
and lead-time constraints to produce procurement-ready batch orders.
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


class SupplierConstraintsTool(CodedTool):
    """
    CodedTool that applies supplier parameters (MOQ, case pack sizes, lead times) to calculate
    actual purchase order quantities and costs.
    """

    def __init__(self) -> None:
        """Initialize the SupplierConstraintsTool."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.abspath(os.path.join(base_dir, "..", "..", "data")),
            os.path.abspath(os.path.join(base_dir, "..", "..", "..", "data")),
            os.path.abspath(os.path.join(os.getcwd(), "data")),
        ]
        self.data_dir = next((d for d in candidates if os.path.isdir(d)), candidates[0])
        logger.info("SupplierConstraintsTool initialized with data_dir: %s", self.data_dir)

    def _load_csv(self, filename: str) -> List[Dict[str, str]]:
        """Load records from a CSV file."""
        file_path = os.path.join(self.data_dir, filename)
        if not os.path.exists(file_path):
            logger.warning("Data file not found: %s", file_path)
            return []
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [dict(row) for row in reader]

    def _calculate_batch_order(self, sku: str, requested_qty: int) -> Dict[str, Any]:
        """Calculate MOQ and case-pack rounded order quantity for a single SKU."""
        suppliers = {s.get("sku"): s for s in self._load_csv("suppliers.csv")}
        products = {p.get("sku"): p for p in self._load_csv("products.csv")}

        sup = suppliers.get(sku)
        prod = products.get(sku, {})

        if not sup:
            return {
                "sku": sku,
                "error": f"No active supplier configured for SKU {sku}",
                "requested_qty": requested_qty,
            }

        supplier_name = sup.get("supplier_name", "Unknown Supplier")
        supplier_id = sup.get("supplier_id")
        lead_time_days = int(sup.get("lead_time_days", 7))
        moq = int(sup.get("minimum_order_qty", 1))
        case_pack = max(1, int(sup.get("case_pack_qty", 1)))
        unit_cost = float(sup.get("unit_cost", prod.get("unit_cost", 0)))
        currency = sup.get("currency", "INR")
        payment_terms = sup.get("payment_terms", "Standard")

        # Round up to nearest case pack
        packs_needed = math.ceil(max(requested_qty, 1) / case_pack)
        pack_rounded_qty = packs_needed * case_pack

        # Enforce Minimum Order Quantity
        final_order_qty = max(moq, pack_rounded_qty)

        # Ensure final quantity still adheres to case pack multiple if MOQ bumped it
        if final_order_qty % case_pack != 0:
            final_order_qty = math.ceil(final_order_qty / case_pack) * case_pack

        total_cost = round(final_order_qty * unit_cost, 2)

        return {
            "sku": sku,
            "product_name": prod.get("product_name", sku),
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "lead_time_days": lead_time_days,
            "payment_terms": payment_terms,
            "unit_cost": unit_cost,
            "currency": currency,
            "requested_qty": requested_qty,
            "minimum_order_qty": moq,
            "case_pack_qty": case_pack,
            "final_batch_qty": final_order_qty,
            "total_line_cost": total_cost,
            "adjustment_reason": (
                f"Requested {requested_qty} units adjusted to {final_order_qty} units "
                f"to satisfy MOQ ({moq}) and Case Pack ({case_pack}x)."
                if final_order_qty != requested_qty
                else "Matches MOQ and pack multiple perfectly."
            ),
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Evaluate supplier constraints for specified items.

        :param args: Dictionary containing:
            - "items": List of dicts with {"sku": str, "quantity": int} OR
            - "sku": str and "quantity": int for single SKU.
        :param sly_data: Context information.
        :return: Procurement order lines with batch adjustments and costs.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        items = args.get("items")
        single_sku = args.get("sku")
        single_qty = args.get("quantity")
        if single_qty is None:
            single_qty = args.get("recommended_units")

        if single_sku and single_qty is not None:
            items = [{"sku": single_sku, "quantity": int(single_qty)}]

        if not items:
            # If no items supplied, check all suppliers and return their terms
            suppliers = self._load_csv("suppliers.csv")
            return {"active_suppliers": suppliers}

        batch_lines = []
        grand_total = 0.0

        for it in items:
            s = it.get("sku")
            q = int(it.get("quantity", 0))
            line = self._calculate_batch_order(s, q)
            batch_lines.append(line)
            if "total_line_cost" in line:
                grand_total += line.get("total_line_cost", 0.0)

        return {
            "procurement_summary": {
                "total_items": len(batch_lines),
                "total_estimated_cost": round(grand_total, 2),
                "currency": "INR",
            },
            "order_lines": batch_lines,
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
