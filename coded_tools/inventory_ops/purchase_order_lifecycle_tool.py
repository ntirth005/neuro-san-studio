"""
Purchase-order lifecycle CodedTool for Neuro-SAN.
Tracks supplier acknowledgements, confirmations, partial receipts, and follow-up drafts.
"""

import asyncio
import logging
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore

logger = logging.getLogger(__name__)


class PurchaseOrderLifecycleTool(CodedTool):
    """
    CodedTool that answers purchase-order status, exception, and receipt questions.
    """

    def __init__(self) -> None:
        """Initialize the tool with the shared data store."""
        self.store = InventoryDataStore()
        logger.info("%s initialized.", self.__class__.__name__)

    def _events(self, po_id: str) -> List[Dict[str, str]]:
        """Return events for a purchase order in chronological order."""
        rows = [row for row in self.store.load("po_events.csv") if row.get("po_id") == po_id]
        rows.sort(key=lambda row: row.get("occurred_at", ""))
        return rows

    def _event_types(self, po_id: str) -> List[str]:
        """Return the event types recorded for a purchase order."""
        return [row.get("event_type", "") for row in self._events(po_id)]

    def _received_quantity(self, po_id: str) -> int:
        """Sum received quantities for a purchase order."""
        return sum(
            self.store.to_int(row.get("received_qty"))
            for row in self.store.load("receipts.csv")
            if row.get("po_id") == po_id
        )

    def _po(self, po_id: str) -> Optional[Dict[str, str]]:
        """Return one purchase order by ID."""
        return next((row for row in self.store.load("purchase_orders.csv") if row.get("po_id") == po_id), None)

    def _open_quantity(self, row: Dict[str, str]) -> int:
        """Return the still-open quantity on a purchase-order line."""
        confirmed = self.store.to_int(row.get("confirmed_qty"))
        requested = self.store.to_int(row.get("requested_qty"))
        ordered = confirmed if confirmed > 0 else requested
        return max(0, ordered - self._received_quantity(row.get("po_id", "")))

    def _exception_queue(self) -> Dict[str, Any]:
        """Rank overdue, delayed, short, and price-changed purchase orders."""
        reference = self.store.reference_time()
        exceptions = []
        for row in self.store.load("purchase_orders.csv"):
            po_id = row.get("po_id", "")
            status = row.get("status", "").upper()
            if status in {"RECEIVED", "CANCELLED", "CLOSED"}:
                continue
            event_types = self._event_types(po_id)
            issues = []
            ack_due = self.store.to_datetime(row.get("ack_due_at"))
            acknowledged = "acknowledged" in event_types
            if not acknowledged and ack_due and reference > ack_due:
                hours_overdue = (reference - ack_due).total_seconds() / 3600.0
                issues.append(
                    {
                        "issue": "OVERDUE_ACK",
                        "hours_overdue": round(hours_overdue, 1),
                        "severity": "HIGH" if hours_overdue > 48 else "MEDIUM",
                    }
                )
            requested_date = self.store.to_datetime(row.get("requested_date"))
            confirmed_date = self.store.to_datetime(row.get("confirmed_date"))
            if requested_date and confirmed_date and confirmed_date > requested_date:
                issues.append(
                    {
                        "issue": "DELAYED",
                        "days_late": (confirmed_date - requested_date).days,
                        "severity": "HIGH" if (confirmed_date - requested_date).days >= 7 else "MEDIUM",
                    }
                )
            confirmed_qty = self.store.to_int(row.get("confirmed_qty"))
            received = self._received_quantity(po_id)
            if confirmed_qty > received and status in {"PARTIALLY_SHIPPED", "PARTIALLY_RECEIVED"}:
                issues.append(
                    {
                        "issue": "SHORT_SHIPMENT",
                        "units_short": confirmed_qty - received,
                        "severity": "HIGH" if confirmed_qty - received >= max(1, confirmed_qty // 2) else "MEDIUM",
                    }
                )
            if "price_change" in event_types:
                issues.append(
                    {
                        "issue": "PRICE_CHANGE",
                        "severity": "MEDIUM",
                    }
                )
            if not issues:
                continue
            open_quantity = self._open_quantity(row)
            unit_cost = self.store.to_float(row.get("unit_cost"))
            exceptions.append(
                {
                    "po_id": po_id,
                    "supplier_id": row.get("supplier_id"),
                    "sku": row.get("sku"),
                    "status": status,
                    "requested_qty": self.store.to_int(row.get("requested_qty")),
                    "confirmed_qty": confirmed_qty,
                    "received_qty": received,
                    "open_qty": open_quantity,
                    "requested_date": row.get("requested_date"),
                    "confirmed_date": row.get("confirmed_date"),
                    "issues": issues,
                    "financial_exposure_inr": round(open_quantity * unit_cost, 2),
                }
            )
        exceptions.sort(
            key=lambda item: (
                max(issue["severity"] == "HIGH" for issue in item["issues"]),
                item["financial_exposure_inr"],
            ),
            reverse=True,
        )
        return {
            "as_of": reference.isoformat(),
            "summary": {
                "exceptions": len(exceptions),
                "high_severity": sum(
                    1 for item in exceptions if any(issue["severity"] == "HIGH" for issue in item["issues"])
                ),
            },
            "exceptions": exceptions,
        }

    def _confirmation_status(self, po_id: str) -> Dict[str, Any]:
        """Distinguish requested dates from supplier-confirmed commitments."""
        row = self._po(po_id)
        if not row:
            return {"error": f"Unknown purchase order: {po_id}"}
        event_types = self._event_types(po_id)
        return {
            "po_id": po_id,
            "supplier_id": row.get("supplier_id"),
            "status": row.get("status"),
            "requested_qty": self.store.to_int(row.get("requested_qty")),
            "confirmed_qty": self.store.to_int(row.get("confirmed_qty")),
            "requested_date": row.get("requested_date"),
            "confirmed_date": row.get("confirmed_date"),
            "acknowledged": "acknowledged" in event_types,
            "date_confirmed_by_supplier": bool(row.get("confirmed_date")),
            "price_changed": "price_change" in event_types,
            "events": self._events(po_id),
        }

    def _draft_followup(self, po_id: str, notes: str = "") -> Dict[str, Any]:
        """Create a follow-up draft without sending it to the supplier."""
        row = self._po(po_id)
        if not row:
            return {"error": f"Unknown purchase order: {po_id}"}
        contacts = {item.get("supplier_id"): item for item in self.store.load("supplier_contacts.csv")}
        contact = contacts.get(row.get("supplier_id"), {})
        event_types = self._event_types(po_id)
        subject = f"Follow-up on purchase order {po_id}"
        body = [
            f"Hello {row.get('supplier_id')} team,",
            "",
            f"We are following up on purchase order {po_id} for {row.get('sku')}.",
            f"Requested quantity: {row.get('requested_qty')} units.",
            f"Requested delivery date: {row.get('requested_date')}.",
        ]
        if "acknowledged" not in event_types:
            body.append("We have not yet received an acknowledgement. Please confirm receipt and the delivery date.")
        else:
            body.append("Please confirm the current shipment date and any quantity changes.")
        if notes:
            body.append(f"Additional context: {notes}")
        body.extend(["", "Thank you,", "Inventory Operations"])
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output", "outbox"
        )
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{po_id}-followup.md")
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join([subject, "", *body]))
        return {
            "po_id": po_id,
            "supplier_id": row.get("supplier_id"),
            "contact": contact.get("contact"),
            "channel": contact.get("channel"),
            "subject": subject,
            "body": "\n".join(body),
            "draft_path": output_path,
            "requires_human_approval": True,
            "sent": False,
        }

    def _receipt_reconciliation(self, po_id: Optional[str] = None) -> Dict[str, Any]:
        """Compare ordered, confirmed, and received quantities."""
        rows = self.store.load("purchase_orders.csv")
        if po_id:
            rows = [row for row in rows if row.get("po_id") == po_id]
        items = []
        for row in rows:
            current_po = row.get("po_id", "")
            requested = self.store.to_int(row.get("requested_qty"))
            confirmed = self.store.to_int(row.get("confirmed_qty"))
            received = self._received_quantity(current_po)
            ordered = confirmed if confirmed > 0 else requested
            if received == 0 and row.get("status", "").upper() not in {"PARTIALLY_SHIPPED", "RECEIVED"}:
                status = "IN_TRANSIT"
            elif received < ordered:
                status = "SHORT"
            elif received > ordered:
                status = "OVER_RECEIVED"
            else:
                status = "MATCH"
            items.append(
                {
                    "po_id": current_po,
                    "sku": row.get("sku"),
                    "requested_qty": requested,
                    "confirmed_qty": confirmed,
                    "received_qty": received,
                    "open_qty": max(0, ordered - received),
                    "reconciliation_status": status,
                }
            )
        return {
            "summary": {
                "purchase_orders": len(items),
                "short_or_open": sum(1 for item in items if item["reconciliation_status"] in {"SHORT", "IN_TRANSIT"}),
            },
            "items": items,
        }

    def _partial_fulfillment(self) -> Dict[str, Any]:
        """List purchase orders with outstanding backorders."""
        items = []
        for row in self.store.load("purchase_orders.csv"):
            po_id = row.get("po_id", "")
            confirmed = self.store.to_int(row.get("confirmed_qty"))
            requested = self.store.to_int(row.get("requested_qty"))
            ordered = confirmed if confirmed > 0 else requested
            received = self._received_quantity(po_id)
            if received < ordered and row.get("status", "").upper() not in {"CANCELLED", "CLOSED"}:
                items.append(
                    {
                        "po_id": po_id,
                        "supplier_id": row.get("supplier_id"),
                        "sku": row.get("sku"),
                        "ordered_qty": ordered,
                        "received_qty": received,
                        "backorder_qty": ordered - received,
                        "status": row.get("status"),
                    }
                )
        return {
            "summary": {
                "partial_orders": len(items),
                "total_backorder_units": sum(item["backorder_qty"] for item in items),
            },
            "items": items,
        }

    def _escalation_queue(self) -> Dict[str, Any]:
        """Apply follow-up policy to determine escalation levels."""
        policy = {row.get("policy_id"): row for row in self.store.load("followup_policy.csv")}.get("DEFAULT", {})
        escalation_hours = self.store.to_float(policy.get("escalation_hours"), 48.0)
        reference = self.store.reference_time()
        items = []
        for row in self.store.load("purchase_orders.csv"):
            po_id = row.get("po_id", "")
            if row.get("status", "").upper() in {"RECEIVED", "CANCELLED", "CLOSED"}:
                continue
            events = self._events(po_id)
            if any(event.get("event_type") == "acknowledged" for event in events):
                continue
            sent_at = self.store.to_datetime(row.get("sent_at"))
            if not sent_at:
                continue
            hours_waiting = (reference - sent_at).total_seconds() / 3600.0
            if hours_waiting >= escalation_hours:
                level = "LEVEL_2_ESCALATE"
            elif hours_waiting >= 24.0:
                level = "LEVEL_1_REMIND"
            else:
                continue
            items.append(
                {
                    "po_id": po_id,
                    "supplier_id": row.get("supplier_id"),
                    "sku": row.get("sku"),
                    "hours_waiting": round(hours_waiting, 1),
                    "escalation_level": level,
                    "requested_date": row.get("requested_date"),
                }
            )
        items.sort(key=lambda item: item["hours_waiting"], reverse=True)
        return {
            "summary": {
                "escalations": len(items),
                "level_2": sum(1 for item in items if item["escalation_level"] == "LEVEL_2_ESCALATE"),
            },
            "items": items,
        }

    @staticmethod
    def _normalize_action(action: str) -> str:
        """Map natural-language action labels onto the supported action names."""
        normalized = str(action).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "overdue": "exception_queue",
            "overdue_confirmations": "exception_queue",
            "check_overdue_confirmations": "exception_queue",
            "exceptions": "exception_queue",
            "exception": "exception_queue",
            "exception_queue": "exception_queue",
            "confirmation": "confirmation_status",
            "confirmation_status": "confirmation_status",
            "po_status": "confirmation_status",
            "follow_up": "draft_followup",
            "follow_up_drafts": "draft_followup",
            "draft_follow_up": "draft_followup",
            "draft_followup": "draft_followup",
            "receipts": "receipt_reconciliation",
            "reconcile_receipts": "receipt_reconciliation",
            "receipt_reconciliation": "receipt_reconciliation",
            "partial": "partial_fulfillment",
            "backorders": "partial_fulfillment",
            "partial_fulfillment": "partial_fulfillment",
            "escalations": "escalation_queue",
            "escalation": "escalation_queue",
            "escalation_queue": "escalation_queue",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "overdue" in normalized or "exception" in normalized:
            return "exception_queue"
        if "confirmation" in normalized and "status" in normalized:
            return "confirmation_status"
        if "follow" in normalized:
            return "draft_followup"
        if "receipt" in normalized:
            return "receipt_reconciliation"
        if "partial" in normalized or "backorder" in normalized:
            return "partial_fulfillment"
        if "escalat" in normalized:
            return "escalation_queue"
        return normalized

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """
        Execute a purchase-order lifecycle action.

        :param args: Dictionary with "action" and optional "po_id".
        :param sly_data: Unused context dictionary.
        :return: Structured lifecycle result.
        """
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = self._normalize_action(args.get("action", "exception_queue"))
        po_id = self.store.normalize_optional(args.get("po_id"))
        if action == "exception_queue":
            return self._exception_queue()
        if action == "confirmation_status":
            if not po_id:
                return {"error": "confirmation_status requires a po_id"}
            return self._confirmation_status(po_id)
        if action == "draft_followup":
            if not po_id:
                return {"error": "draft_followup requires a po_id"}
            return self._draft_followup(po_id, args.get("notes", ""))
        if action == "receipt_reconciliation":
            return self._receipt_reconciliation(po_id)
        if action == "partial_fulfillment":
            return self._partial_fulfillment()
        if action == "escalation_queue":
            return self._escalation_queue()
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
