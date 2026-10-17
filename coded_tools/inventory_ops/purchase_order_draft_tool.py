"""
Purchase Order Draft CodedTool for Neuro-SAN.
Generates auditable, timestamped purchase order draft documents with budget validation,
line-item breakdowns, supplier details, and delivery date projections.
"""

import asyncio
import datetime
import json
import logging
import os
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

logger = logging.getLogger(__name__)


class PurchaseOrderDraftTool(CodedTool):
    """
    CodedTool that generates structured Purchase Order draft artifacts.
    """

    def __init__(self) -> None:
        """Initialize the PurchaseOrderDraftTool."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.abspath(os.path.join(base_dir, "..", "..", "output", "purchase_orders")),
            os.path.abspath(os.path.join(base_dir, "..", "..", "..", "output", "purchase_orders")),
            os.path.abspath(os.path.join(os.getcwd(), "output", "purchase_orders")),
        ]
        self.output_dir = candidates[0]
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info("PurchaseOrderDraftTool initialized with output_dir: %s", self.output_dir)

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Create a purchase order draft.

        :param args: Dictionary containing:
            - "order_lines": List of line items with sku, quantity, unit_cost, supplier_name, etc.
            - "title": (Optional) PO Title.
            - "max_budget": (Optional) Maximum budget threshold in currency units.
            - "notes": (Optional) Justification notes from the operations team.
        :param sly_data: Context information.
        :return: Dict with generated PO details and saved file path.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        order_lines: List[Dict[str, Any]] = args.get("order_lines") or args.get("order_items") or []
        title: str = args.get("title") or "Inventory Replenishment Order"
        max_budget = args.get("max_budget") or args.get("budget_limit")
        notes: str = args.get("notes") or "Auto-generated draft based on demand velocity and lead-time audit."

        if not order_lines:
            return {"error": "Cannot create purchase order draft without order_lines"}

        now = datetime.datetime.now()
        po_number = f"PO-{now.strftime('%Y%m%d')}-{random.randint(1000, 9999)}"

        total_amount = 0.0
        currency = "INR"

        table_rows = []
        for line in order_lines:
            sku = line.get("sku") or "UNKNOWN"
            name = line.get("product_name") or line.get("sku_name") or sku
            qty = int(line.get("final_batch_qty") or line.get("quantity") or line.get("units") or 0)
            unit_cost = float(line.get("unit_cost") or 0.0)
            line_cost = round(qty * unit_cost, 2)
            total_amount += line_cost
            supplier = line.get("supplier_name") or line.get("supplier_id") or "Primary Supplier"
            lead_time = int(line.get("lead_time_days") or 7)
            expected_delivery = (now + datetime.timedelta(days=lead_time)).strftime("%Y-%m-%d")

            table_rows.append(
                {
                    "sku": sku,
                    "product_name": name,
                    "supplier": supplier,
                    "quantity": qty,
                    "unit_cost": unit_cost,
                    "line_cost": line_cost,
                    "expected_delivery": expected_delivery,
                }
            )

        budget_warning = None
        if max_budget is not None and total_amount > float(max_budget):
            budget_warning = (
                f"EXCEEDS BUDGET: Total order amount ({currency} {total_amount:,.2f}) "
                f"exceeds authorized budget limit ({currency} {float(max_budget):,.2f})."
            )

        # Build Markdown Document
        md_content = [
            f"# PURCHASE ORDER DRAFT — {po_number}",
            f"**Title:** {title}  ",
            f"**Created Date:** {now.strftime('%B %d, %Y %H:%M:%S')}  ",
            "**Status:** PENDING_MERCHANT_APPROVAL  ",
            f"**Currency:** {currency}  ",
            "",
            "---",
            "",
            "## 1. Line Items Breakdown",
            "",
            "| SKU | Product Description | Supplier | Qty | Unit Cost | Total Cost | Est. Delivery |",
            "|---|---|---|---:|---:|---:|---|",
        ]

        for r in table_rows:
            md_content.append(
                f"| `{r['sku']}` | {r['product_name']} | {r['supplier']} | "
                f"{r['quantity']} | {currency} {r['unit_cost']:.2f} | "
                f"{currency} {r['line_cost']:.2f} | {r['expected_delivery']} |"
            )

        md_content.extend(
            [
                "",
                f"**Grand Total:** {currency} {total_amount:,.2f}",
                "",
                "---",
                "",
                "## 2. Operational Justification & Audit Notes",
                notes,
                "",
            ]
        )

        if budget_warning:
            md_content.extend(
                [
                    "> [!WARNING]",
                    f"> **Budget Variance Notice:** {budget_warning}",
                    "",
                ]
            )

        md_content.extend(
            [
                "## 3. Approval Sign-off",
                "- [ ] Inventory Lead Review",
                "- [ ] Finance / Working Capital Sign-off",
                "- [ ] Final Order Confirmation",
                "",
                "*Note: This document is an auditable internal draft. No funds are committed until human sign-off.*",
            ]
        )

        file_name = f"{po_number}.md"
        file_path = os.path.join(self.output_dir, file_name)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_content))

        # Also write JSON metadata
        json_path = os.path.join(self.output_dir, f"{po_number}.json")
        po_dict = {
            "po_number": po_number,
            "title": title,
            "created_at": now.isoformat(),
            "status": "PENDING_MERCHANT_APPROVAL",
            "currency": currency,
            "total_amount": round(total_amount, 2),
            "max_budget": max_budget,
            "budget_warning": budget_warning,
            "items": table_rows,
            "file_path": file_path,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(po_dict, f, indent=2)

        logger.info("Successfully created Purchase Order Draft at: %s", file_path)

        return {
            "po_number": po_number,
            "total_amount": round(total_amount, 2),
            "currency": currency,
            "budget_warning": budget_warning,
            "items_count": len(table_rows),
            "markdown_path": file_path,
            "json_path": json_path,
            "summary_preview": (
                f"Generated Purchase Order Draft {po_number} with {len(table_rows)} items "
                f"totaling {currency} {total_amount:,.2f}."
            ),
        }

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
