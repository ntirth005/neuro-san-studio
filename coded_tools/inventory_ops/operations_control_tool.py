"""
Operations control CodedTool for Neuro-SAN.
Provides the daily exception queue, change summaries, approvals, integration health,
idempotent PO drafts, approval-gated sends, reversals, recommendation explanations,
and action-by-action status.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.inventory_ops.data_store import InventoryDataStore
from coded_tools.inventory_ops.demand_forecast_tool import DemandForecastTool
from coded_tools.inventory_ops.inventory_quality_tool import InventoryQualityTool
from coded_tools.inventory_ops.inventory_reconciliation_tool import InventoryReconciliationTool
from coded_tools.inventory_ops.purchase_order_draft_tool import PurchaseOrderDraftTool
from coded_tools.inventory_ops.purchase_order_lifecycle_tool import PurchaseOrderLifecycleTool

logger = logging.getLogger(__name__)


class OperationsControlTool(CodedTool):
    """Deterministic daily-operations, approval, and audit engine."""

    def __init__(self) -> None:
        """Initialize with the shared data store and dependent tools."""
        self.store = InventoryDataStore()
        self.reconciliation = InventoryReconciliationTool()
        self.forecast = DemandForecastTool()
        self.quality = InventoryQualityTool()
        self.po_lifecycle = PurchaseOrderLifecycleTool()
        self.po_draft = PurchaseOrderDraftTool()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.audit_dir = os.path.abspath(os.path.join(base_dir, "..", "..", "output", "audit"))
        os.makedirs(self.audit_dir, exist_ok=True)
        self.runtime_log = os.path.join(self.audit_dir, "operations_actions.jsonl")
        logger.info("%s initialized with audit_dir: %s", self.__class__.__name__, self.audit_dir)

    @staticmethod
    def _normalize_action(action: str) -> str:
        """Map natural-language action labels onto supported actions."""
        normalized = str(action).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "daily_queue": "daily_exception_queue",
            "urgent_problems": "daily_exception_queue",
            "exception_queue": "daily_exception_queue",
            "changes": "change_since_yesterday",
            "what_changed": "change_since_yesterday",
            "approvals": "pending_approvals",
            "approval_queue": "pending_approvals",
            "recommendation_change": "recommendation_history",
            "why_changed": "recommendation_history",
            "integration": "integration_health",
            "api_health": "integration_health",
            "create_po": "create_po_draft",
            "po_draft": "create_po_draft",
            "send_po": "send_po",
            "send_purchase_order": "send_po",
            "undo": "reverse_action",
            "correct_action": "reverse_action",
            "reversal": "reverse_action",
            "explain": "explain_recommendation",
            "why_recommend": "explain_recommendation",
            "status": "action_status",
            "task_status": "action_status",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "queue" in normalized and ("daily" in normalized or "urgent" in normalized):
            return "daily_exception_queue"
        if "change" in normalized:
            return "change_since_yesterday"
        if "approval" in normalized:
            return "pending_approvals"
        if "integration" in normalized or "health" in normalized:
            return "integration_health"
        if "send" in normalized:
            return "send_po"
        if "draft" in normalized or "create_po" in normalized:
            return "create_po_draft"
        if "undo" in normalized or "reverse" in normalized or "correct" in normalized:
            return "reverse_action"
        if "explain" in normalized:
            return "explain_recommendation"
        if "status" in normalized:
            return "action_status"
        if "recommend" in normalized:
            return "recommendation_history"
        return normalized

    def _runtime_actions(self) -> List[Dict[str, Any]]:
        """Read runtime audit actions written by this tool."""
        if not os.path.exists(self.runtime_log):
            return []
        actions = []
        with open(self.runtime_log, mode="r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    actions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return actions

    def _all_actions(self) -> List[Dict[str, Any]]:
        """Return seeded and runtime actions."""
        seeded = [{**row, "runtime": False} for row in self.store.action_log()]
        runtime = [{**row, "runtime": True} for row in self._runtime_actions()]
        return seeded + runtime

    def _append_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Append an action to the runtime audit log."""
        with open(self.runtime_log, mode="a", encoding="utf-8") as handle:
            handle.write(json.dumps(action, sort_keys=True) + "\n")
        return action

    @staticmethod
    def _action_id(prefix: str, payload: str) -> str:
        """Create a stable action identifier."""
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
        return f"{prefix}-{digest}"

    def _daily_exception_queue(self, limit: int) -> Dict[str, Any]:
        """Build a prioritized queue of the most urgent inventory problems."""
        issues = []
        data_quality = self.reconciliation.invoke({"action": "data_quality"}, {})
        if isinstance(data_quality, dict):
            for issue in data_quality.get("issues", []):
                severity = issue.get("severity", "MEDIUM")
                score = 300 if severity == "HIGH" else 120
                issues.append(
                    {
                        "source": "inventory_integrity",
                        "severity": severity,
                        "title": issue.get("issue"),
                        "sku": issue.get("sku"),
                        "location_id": issue.get("location_id"),
                        "evidence": issue,
                        "financial_exposure_inr": 0.0,
                        "score": score,
                    }
                )
        stockouts = self.forecast.invoke({"action": "stockout_horizon", "horizon_days": 14}, {})
        if isinstance(stockouts, dict):
            for item in stockouts.get("items", []):
                runout = self.store.to_float(item.get("runout_days"))
                exposure = self.store.to_float(item.get("expected_lost_gmv_inr"))
                issues.append(
                    {
                        "source": "demand_forecast",
                        "severity": "CRITICAL" if runout <= 7 else "WARNING",
                        "title": f"Stockout in {runout} days",
                        "sku": item.get("sku"),
                        "location_id": None,
                        "evidence": item,
                        "financial_exposure_inr": exposure,
                        "score": 400 + exposure / 1000.0 + max(0.0, 14 - runout) * 10,
                    }
                )
        po_queue = self.po_lifecycle.invoke({"action": "exception_queue"}, {})
        if isinstance(po_queue, dict):
            for item in po_queue.get("exceptions", []):
                exposure = self.store.to_float(item.get("financial_exposure_inr"))
                issues.append(
                    {
                        "source": "supplier_coordination",
                        "severity": "HIGH",
                        "title": "Purchase-order exception",
                        "sku": item.get("sku"),
                        "location_id": None,
                        "evidence": item,
                        "financial_exposure_inr": exposure,
                        "score": 250 + exposure / 1000.0,
                    }
                )
        recall = self.quality.invoke({"action": "recall_check"}, {})
        if isinstance(recall, dict) and recall.get("summary", {}).get("selling_recalled_stock"):
            exposure = self.store.to_float(recall.get("summary", {}).get("channel_exposure_units"))
            issues.append(
                {
                    "source": "quality_control",
                    "severity": "CRITICAL",
                    "title": "Recalled stock still exposed for sale",
                    "sku": ",".join(recall.get("summary", {}).get("recalled_skus", [])),
                    "location_id": None,
                    "evidence": recall.get("summary"),
                    "financial_exposure_inr": exposure * 1800.0,
                    "score": 600 + exposure * 10,
                }
            )
        approvals = self._pending_approvals()
        for item in approvals.get("items", []):
            if item.get("overdue"):
                issues.append(
                    {
                        "source": "approvals",
                        "severity": "HIGH" if item.get("escalation") == "LEVEL_3" else "MEDIUM",
                        "title": "Approval past SLA",
                        "sku": None,
                        "location_id": None,
                        "evidence": item,
                        "financial_exposure_inr": self.store.to_float(item.get("amount_inr")),
                        "score": 180 + self.store.to_float(item.get("age_hours")),
                    }
                )
        issues.sort(key=lambda item: item["score"], reverse=True)
        top = issues[:limit]
        for rank, issue in enumerate(top, start=1):
            issue["rank"] = rank
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "issues_found": len(issues),
                "top_issues": len(top),
                "critical": sum(1 for item in top if item["severity"] == "CRITICAL"),
                "total_financial_exposure_inr": round(sum(item["financial_exposure_inr"] for item in top), 2),
            },
            "items": top,
        }

    def _change_since_yesterday(self) -> Dict[str, Any]:
        """Summarize material inventory and operational changes since yesterday."""
        snapshots = self.store.inventory_snapshots()
        dates = sorted({row.get("snapshot_date", "") for row in snapshots if row.get("snapshot_date")})
        if len(dates) < 2:
            return {"error": "At least two inventory snapshots are required"}
        latest_date, previous_date = dates[-1], dates[-2]
        latest = {
            (row.get("sku"), row.get("location_id")): row
            for row in snapshots
            if row.get("snapshot_date") == latest_date
        }
        previous = {
            (row.get("sku"), row.get("location_id")): row
            for row in snapshots
            if row.get("snapshot_date") == previous_date
        }
        changes = []
        for key in sorted(set(latest) | set(previous)):
            current = latest.get(key, {})
            prior = previous.get(key, {})
            current_qty = self.store.to_float(current.get("on_hand_qty"))
            prior_qty = self.store.to_float(prior.get("on_hand_qty"))
            qty_delta = current_qty - prior_qty
            value_delta = self.store.to_float(current.get("inventory_value_inr")) - self.store.to_float(
                prior.get("inventory_value_inr")
            )
            if abs(qty_delta) < 1 and abs(value_delta) < 500:
                continue
            changes.append(
                {
                    "sku": key[0],
                    "location_id": key[1],
                    "on_hand_yesterday": int(prior_qty),
                    "on_hand_today": int(current_qty),
                    "quantity_delta": int(qty_delta),
                    "value_delta_inr": round(value_delta, 2),
                }
            )
        new_events = []
        for row in self.store.load("purchase_orders.csv"):
            if row.get("sent_at", "")[:10] == latest_date:
                new_events.append(
                    {"type": "purchase_order", "reference": row.get("po_id"), "detail": row.get("status")}
                )
        for row in self.store.load("returns.csv"):
            if row.get("return_date", "")[:10] == latest_date:
                new_events.append(
                    {"type": "return", "reference": row.get("return_id"), "detail": row.get("condition")}
                )
        for row in self.store.quality_events():
            if row.get("detected_at", "")[:10] == latest_date:
                new_events.append(
                    {"type": "quality_event", "reference": row.get("event_id"), "detail": row.get("event_type")}
                )
        for row in self.store.transfers():
            if row.get("created_at", "")[:10] == latest_date:
                new_events.append(
                    {"type": "transfer", "reference": row.get("transfer_id"), "detail": row.get("status")}
                )
        for row in self.store.approvals():
            if row.get("requested_at", "")[:10] == latest_date:
                new_events.append({"type": "approval", "reference": row.get("task_id"), "detail": row.get("status")})
        for row in self.store.recommendation_history():
            if row.get("generated_at", "")[:10] == latest_date:
                new_events.append(
                    {
                        "type": "recommendation",
                        "reference": row.get("recommendation_id"),
                        "detail": row.get("recommendation"),
                    }
                )
        return {
            "as_of": self.store.reference_time().isoformat(),
            "comparison": {"previous_snapshot": previous_date, "latest_snapshot": latest_date},
            "summary": {
                "material_inventory_changes": len(changes),
                "new_operational_events": len(new_events),
            },
            "inventory_changes": changes,
            "new_events": new_events,
        }

    def _pending_approvals(self) -> Dict[str, Any]:
        """List pending approvals and escalate those past SLA."""
        reference = self.store.reference_time()
        items = []
        for row in self.store.approvals():
            if row.get("status", "").lower() != "pending":
                continue
            requested = self.store.to_datetime(row.get("requested_at"))
            age_hours = (reference - requested).total_seconds() / 3600.0 if requested else 0.0
            sla = self.store.to_float(row.get("sla_hours"), 24.0)
            overdue = age_hours > sla
            items.append(
                {
                    "task_id": row.get("task_id"),
                    "task_type": row.get("task_type"),
                    "reference_id": row.get("reference_id"),
                    "amount_inr": self.store.to_float(row.get("amount_inr")),
                    "requested_at": row.get("requested_at"),
                    "age_hours": round(age_hours, 1),
                    "sla_hours": int(sla),
                    "overdue": overdue,
                    "escalation": ("LEVEL_3" if age_hours > sla * 2 else "LEVEL_2" if overdue else "NORMAL"),
                    "approver": row.get("approver"),
                    "notes": row.get("notes"),
                }
            )
        items.sort(key=lambda item: item["age_hours"], reverse=True)
        return {
            "as_of": reference.isoformat(),
            "summary": {
                "pending": len(items),
                "overdue": sum(1 for item in items if item["overdue"]),
                "level_3": sum(1 for item in items if item["escalation"] == "LEVEL_3"),
            },
            "items": items,
        }

    def _recommendation_history(self, sku: Optional[str], recommendation_id: Optional[str]) -> Dict[str, Any]:
        """Explain why a recommendation changed."""
        rows = self.store.recommendation_history()
        if recommendation_id:
            rows = [row for row in rows if row.get("recommendation_id") == recommendation_id]
        if sku:
            rows = [row for row in rows if row.get("sku") == sku]
        rows = sorted(rows, key=lambda row: row.get("generated_at", ""))
        if not rows:
            return {"error": "No recommendation history found for the requested filter"}
        comparisons = []
        for index in range(1, len(rows)):
            previous = rows[index - 1]
            current = rows[index]
            changed_fields = [
                field
                for field in ["recommendation", "policy_version", "model_version", "inputs_hash"]
                if previous.get(field) != current.get(field)
            ]
            comparisons.append(
                {
                    "sku": current.get("sku"),
                    "previous_recommendation_id": previous.get("recommendation_id"),
                    "current_recommendation_id": current.get("recommendation_id"),
                    "previous_recommendation": previous.get("recommendation"),
                    "current_recommendation": current.get("recommendation"),
                    "changed_fields": changed_fields,
                    "change_reason": current.get("change_reason"),
                    "generated_at": current.get("generated_at"),
                }
            )
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "records": len(rows),
                "comparisons": len(comparisons),
                "latest": rows[-1],
            },
            "history": rows,
            "comparisons": comparisons,
        }

    def _integration_health(self) -> Dict[str, Any]:
        """Report stale or unavailable integrations and block unsafe actions."""
        reference = self.store.reference_time()
        items = []
        blocked_actions = set()
        for row in self.store.integration_health():
            last_success = self.store.to_datetime(row.get("last_success_at"))
            age_minutes = (reference - last_success).total_seconds() / 60.0 if last_success else None
            expected = self.store.to_float(row.get("expected_interval_minutes"), 60.0)
            status = row.get("status", "").lower()
            stale = status != "ok" or (age_minutes is not None and age_minutes > expected * 2)
            if stale:
                if row.get("system") == "marketplace_api":
                    blocked_actions.update(
                        {"marketplace_listing_update", "recall_channel_block", "channel_inventory_sync"}
                    )
                if row.get("system") == "email":
                    blocked_actions.add("po_send")
                if row.get("system") == "wms":
                    blocked_actions.update({"transfer_execution", "inventory_adjustment"})
            items.append(
                {
                    "system": row.get("system"),
                    "status": status,
                    "last_success_at": row.get("last_success_at"),
                    "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
                    "expected_interval_minutes": int(expected),
                    "stale": stale,
                    "error_message": row.get("error_message"),
                }
            )
        sync_rows = self.store.load("sync_status.csv")
        stale_locations = []
        for row in sync_rows:
            last_sync = self.store.to_datetime(row.get("last_sync_at"))
            age = (reference - last_sync).total_seconds() / 60.0 if last_sync else None
            expected = self.store.to_float(row.get("expected_interval_minutes"), 60.0)
            if age is not None and age > expected * 2:
                stale_locations.append(
                    {
                        "location_id": row.get("location_id"),
                        "age_minutes": round(age, 1),
                        "status": row.get("status"),
                    }
                )
        return {
            "as_of": reference.isoformat(),
            "summary": {
                "systems_checked": len(items),
                "stale_systems": sum(1 for item in items if item["stale"]),
                "stale_locations": len(stale_locations),
                "safe_mode": bool(blocked_actions),
                "workflow_state_preserved": True,
            },
            "systems": items,
            "stale_locations": stale_locations,
            "blocked_actions": sorted(blocked_actions),
            "guidance": (
                "Queue state-changing actions until the integration recovers; do not retry blindly."
                if blocked_actions
                else "All integrations are healthy for the supported local workflows."
            ),
        }

    def _normalize_order_items(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize agent-provided order items into draft lines."""
        items = args.get("order_items") or args.get("order_lines") or []
        if not items and args.get("sku"):
            items = [{"sku": args.get("sku"), "quantity": args.get("quantity", 1)}]
        products = self.store.products()
        suppliers = self.store.suppliers()
        lines = []
        for item in items:
            sku = item.get("sku", "")
            product = products.get(sku, {})
            supplier = suppliers.get(sku, {})
            quantity = self.store.to_int(item.get("quantity") or item.get("units") or 0)
            if not sku or quantity <= 0:
                continue
            lines.append(
                {
                    "sku": sku,
                    "product_name": item.get("product_name") or product.get("product_name") or sku,
                    "quantity": quantity,
                    "unit_cost": self.store.to_float(item.get("unit_cost") or product.get("unit_cost")),
                    "supplier_name": (
                        item.get("supplier_name") or supplier.get("supplier_name") or "Primary Supplier"
                    ),
                    "lead_time_days": self.store.to_int(
                        item.get("lead_time_days") or supplier.get("lead_time_days"), 7
                    ),
                }
            )
        return lines

    def _create_po_draft(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create an idempotent purchase-order draft."""
        lines = self._normalize_order_items(args)
        if not lines:
            return {"error": "create_po_draft requires at least one valid order item"}
        budget = self.store.to_float(args.get("budget_limit"), 0.0) or None
        notes = args.get("notes", "Created by Operations_Controller")
        total = round(sum(line["quantity"] * line["unit_cost"] for line in lines), 2)
        if budget is not None and total > budget:
            return {
                "error": "Order exceeds the authorized budget",
                "total_amount_inr": total,
                "budget_limit_inr": budget,
            }
        key = args.get("idempotency_key") or self._action_id(
            "IDEM", json.dumps({"lines": lines, "budget": budget}, sort_keys=True)
        )
        for action in self._all_actions():
            if action.get("idempotency_key") == key and action.get("status") == "completed":
                return {
                    "idempotent_replay": True,
                    "idempotency_key": key,
                    "prior_action": action,
                    "message": "An identical draft already exists; returning the prior audit record.",
                }
        result = self.po_draft.invoke(
            {
                "order_lines": lines,
                "max_budget": budget,
                "notes": notes,
            },
            {},
        )
        if isinstance(result, str):
            return {"error": result}
        action = {
            "store_id": "demo-store",
            "action_id": self._action_id("ACT-RUN", key + str(total)),
            "action_type": "po_draft",
            "reference_id": result.get("po_number"),
            "idempotency_key": key,
            "requested_at": self.store.reference_time().isoformat(),
            "completed_at": self.store.reference_time().isoformat(),
            "status": "completed",
            "result": result,
            "error": "",
            "reversal_of": "",
        }
        self._append_action(action)
        return {
            "idempotent_replay": False,
            "idempotency_key": key,
            "draft": result,
            "action": action,
        }

    def _send_po(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Send a PO only when approval, limit, and integration checks pass."""
        po_id = self.store.normalize_optional(args.get("po_id"))
        if not po_id:
            return {"error": "send_po requires a po_id"}
        po = next((row for row in self.store.load("purchase_orders.csv") if row.get("po_id") == po_id), None)
        draft_action = next(
            (
                action
                for action in self._all_actions()
                if action.get("action_type") == "po_draft"
                and action.get("reference_id") == po_id
                and action.get("status") == "completed"
            ),
            None,
        )
        if not po and not draft_action:
            return {"error": f"Purchase order {po_id} not found"}
        if po:
            amount = self.store.to_float(po.get("requested_qty")) * self.store.to_float(po.get("unit_cost"))
        else:
            draft = draft_action.get("result") or {}
            amount = self.store.to_float(draft.get("total_amount"))
            if amount <= 0:
                amount = sum(
                    self.store.to_float(item.get("quantity")) * self.store.to_float(item.get("unit_cost"))
                    for item in draft.get("items", [])
                )
        approval_id = self.store.normalize_optional(args.get("approval_id"))
        approvals = self.store.approvals()
        approval = None
        if approval_id:
            approval = next((row for row in approvals if row.get("task_id") == approval_id), None)
        else:
            approval = next(
                (
                    row
                    for row in approvals
                    if row.get("reference_id") == po_id
                    and row.get("task_type") == "purchase_order_send"
                    and row.get("status", "").lower() == "approved"
                ),
                None,
            )
        policy = next((row for row in self.store.policy_history() if not row.get("effective_to")), {})
        automation_limit = self.store.to_float(policy.get("automation_limit_inr"), 25000.0)
        health = self._integration_health()
        email = next((row for row in health["systems"] if row.get("system") == "email"), {})
        blocks = []
        if not approval or approval.get("status", "").lower() != "approved":
            blocks.append("No approved purchase_order_send task for this PO.")
        if amount > automation_limit:
            blocks.append(f"Amount INR {amount:,.2f} exceeds automation limit INR {automation_limit:,.2f}.")
        if email.get("stale"):
            blocks.append("Email integration is stale or unavailable.")
        if blocks:
            action = {
                "store_id": "demo-store",
                "action_id": self._action_id("ACT-SEND", po_id + str(blocks)),
                "action_type": "po_send",
                "reference_id": po_id,
                "idempotency_key": f"send-{po_id}",
                "requested_at": self.store.reference_time().isoformat(),
                "completed_at": "",
                "status": "blocked",
                "result": "",
                "error": "; ".join(blocks),
                "reversal_of": "",
            }
            self._append_action(action)
            return {
                "sent": False,
                "blocked": True,
                "status": "blocked",
                "po_id": po_id,
                "amount_inr": round(amount, 2),
                "reasons": blocks,
                "message": "Send blocked: " + "; ".join(blocks),
                "required_action": "Obtain approval, reduce the amount, or restore the integration.",
                "action": action,
            }
        for action in self._all_actions():
            if (
                action.get("action_type") == "po_send"
                and action.get("reference_id") == po_id
                and action.get("status") == "completed"
            ):
                return {
                    "sent": True,
                    "simulated": True,
                    "external_side_effect": False,
                    "idempotent_replay": True,
                    "status": "sent_simulated",
                    "po_id": po_id,
                    "amount_inr": round(amount, 2),
                    "approval_id": approval.get("task_id") if approval else None,
                    "message": (
                        "This PO already has a completed simulated send in the audit log; "
                        "no duplicate send was performed and no external email was transmitted."
                    ),
                    "prior_action": action,
                }
        action = {
            "store_id": "demo-store",
            "action_id": self._action_id("ACT-SEND", po_id + "approved"),
            "action_type": "po_send",
            "reference_id": po_id,
            "idempotency_key": f"send-{po_id}",
            "requested_at": self.store.reference_time().isoformat(),
            "completed_at": self.store.reference_time().isoformat(),
            "status": "completed",
            "result": "Simulated supplier transmission; no external email was sent.",
            "error": "",
            "reversal_of": "",
        }
        self._append_action(action)
        return {
            "sent": True,
            "simulated": True,
            "external_side_effect": False,
            "status": "sent_simulated",
            "po_id": po_id,
            "amount_inr": round(amount, 2),
            "approval_id": approval.get("task_id") if approval else None,
            "message": "Simulated send completed; no external email was transmitted.",
            "action": action,
        }

    def _reverse_action(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a compensating action while preserving the original audit record."""
        action_id = self.store.normalize_optional(args.get("action_id"))
        if not action_id:
            return {"error": "reverse_action requires an action_id"}
        original = next((row for row in self._all_actions() if row.get("action_id") == action_id), None)
        if not original:
            return {"error": f"Action {action_id} not found"}
        existing = next((row for row in self._all_actions() if row.get("reversal_of") == action_id), None)
        if existing:
            return {"already_reversed": True, "reversal": existing}
        action_type = original.get("action_type")
        result_text = str(original.get("result", ""))
        match = re.search(r"Adjusted\s+(-?\d+)\s+unit", result_text)
        reversal_units = -int(match.group(1)) if match else None
        if action_type == "inventory_adjustment" and reversal_units is not None:
            status = "completed"
            procedure = (
                f"Post a compensating adjustment of {reversal_units:+d} units to the same SKU/location; "
                "the original adjustment remains in the audit trail."
            )
            requires_approval = False
        elif action_type == "po_send":
            status = "blocked"
            procedure = "Contact the supplier to cancel or amend the PO; a sent PO cannot be unsent."
            requires_approval = True
        elif action_type == "transfer_execution":
            status = "pending"
            procedure = "Create a reverse transfer and obtain operations approval before execution."
            requires_approval = True
        elif action_type == "recall_block":
            status = "pending"
            procedure = "Unblock only after quality approval confirms the batch is safe."
            requires_approval = True
        else:
            status = "pending"
            procedure = "Route to a human operator for a supported compensating action."
            requires_approval = True
        reversal = {
            "store_id": "demo-store",
            "action_id": self._action_id("ACT-REV", action_id),
            "action_type": f"{action_type}_reversal",
            "reference_id": original.get("reference_id"),
            "idempotency_key": f"reverse-{action_id}",
            "requested_at": self.store.reference_time().isoformat(),
            "completed_at": self.store.reference_time().isoformat() if status == "completed" else "",
            "status": status,
            "result": procedure,
            "error": "",
            "reversal_of": action_id,
        }
        self._append_action(reversal)
        return {
            "reversed": status == "completed",
            "requires_human_approval": requires_approval,
            "reversal_units": reversal_units,
            "procedure": procedure,
            "original": original,
            "reversal": reversal,
        }

    def _explain_recommendation(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Explain the evidence, constraints, assumptions, and alternatives behind a recommendation."""
        recommendation_id = self.store.normalize_optional(args.get("recommendation_id"))
        sku = self.store.normalize_optional(args.get("sku"))
        rows = self.store.recommendation_history()
        if recommendation_id:
            target = next((row for row in rows if row.get("recommendation_id") == recommendation_id), None)
        elif sku:
            target = next(
                (
                    row
                    for row in sorted(rows, key=lambda row: row.get("generated_at", ""), reverse=True)
                    if row.get("sku") == sku
                ),
                None,
            )
        else:
            target = None
        if not target:
            return {"error": "Recommendation not found"}
        try:
            evidence = json.loads(target.get("evidence_json", "{}"))
        except json.JSONDecodeError:
            evidence = {}
        predecessor = next((row for row in rows if row.get("superseded_by") == target.get("recommendation_id")), None)
        alternatives = [
            {
                "option": "Hold cash and do not replenish",
                "tradeoff": "Preserves cash but leaves the projected margin at risk.",
            },
            {
                "option": "Order the supplier MOQ instead of the recommended quantity",
                "tradeoff": "May reduce unit cost but can increase excess inventory and carrying cost.",
            },
            {
                "option": "Use an alternate supplier",
                "tradeoff": "Can shorten lead time but may increase unit cost or reduce reliability.",
            },
        ]
        return {
            "as_of": self.store.reference_time().isoformat(),
            "recommendation": target,
            "evidence": evidence,
            "constraints": [
                "Supplier MOQ and case-pack rounding",
                "Budget and automation limit",
                "Lead-time demand and safety stock",
            ],
            "assumptions": [
                "Recent demand velocity is representative of the near-term future.",
                "Supplier lead time and reliability remain within the observed range.",
            ],
            "alternatives": alternatives,
            "why_changed": (
                {
                    "previous_recommendation": predecessor.get("recommendation"),
                    "current_recommendation": target.get("recommendation"),
                    "change_reason": target.get("change_reason"),
                }
                if predecessor
                else None
            ),
            "confidence": "MEDIUM",
        }

    def _action_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return action-by-action status and evidence."""
        action_id = self.store.normalize_optional(args.get("action_id"))
        reference_id = self.store.normalize_optional(args.get("reference_id"))
        actions = self._all_actions()
        if action_id:
            actions = [row for row in actions if row.get("action_id") == action_id]
        if reference_id:
            actions = [row for row in actions if row.get("reference_id") == reference_id]
        actions = sorted(actions, key=lambda row: row.get("requested_at", ""))
        return {
            "as_of": self.store.reference_time().isoformat(),
            "summary": {
                "actions": len(actions),
                "completed": sum(1 for row in actions if row.get("status") == "completed"),
                "failed": sum(1 for row in actions if row.get("status") == "failed"),
                "pending": sum(1 for row in actions if row.get("status") == "pending"),
                "blocked": sum(1 for row in actions if row.get("status") == "blocked"),
            },
            "items": actions,
            "guardrail": "A task is only reported as successful when the audit record is completed.",
        }

    def invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Execute an operations-control action."""
        logger.info("Executing %s with args: %s", self.__class__.__name__, args)
        action = self._normalize_action(args.get("action", "daily_exception_queue"))
        action_hint = self.store.normalize_optional(args.get("action_id"))
        if action == "action_status" and action_hint in {
            "send_po", "create_po_draft", "daily_exception_queue", "change_since_yesterday",
            "pending_approvals", "recommendation_history", "integration_health",
            "reverse_action", "explain_recommendation",
        }:
            action = action_hint
        sku = self.store.normalize_optional(args.get("sku"))
        recommendation_id = self.store.normalize_optional(args.get("recommendation_id"))
        limit = self.store.to_int(args.get("limit"), 5)

        if action == "daily_exception_queue":
            return self._daily_exception_queue(limit)
        if action == "change_since_yesterday":
            return self._change_since_yesterday()
        if action == "pending_approvals":
            return self._pending_approvals()
        if action == "recommendation_history":
            return self._recommendation_history(sku, recommendation_id)
        if action == "integration_health":
            return self._integration_health()
        if action == "create_po_draft":
            return self._create_po_draft(args)
        if action == "send_po":
            return self._send_po(args)
        if action == "reverse_action":
            return self._reverse_action(args)
        if action == "explain_recommendation":
            return self._explain_recommendation(args)
        if action == "action_status":
            return self._action_status(args)
        return {"error": f"Unknown action: {action}"}

    async def async_invoke(self, args: Dict[str, Any], sly_data: Dict[str, Any]) -> Union[Dict[str, Any], str]:
        """Asynchronously delegate to synchronous invoke."""
        return await asyncio.to_thread(self.invoke, args, sly_data)
