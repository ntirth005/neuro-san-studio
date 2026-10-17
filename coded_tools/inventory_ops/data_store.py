"""
Shared data access for the inventory operations coded tools.
"""

import csv
import datetime
import logging
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

logger = logging.getLogger(__name__)


class InventoryDataStore:
    """
    Resolves the synthetic data directory, caches CSV reads, and provides typed helpers.
    """

    _cache: Dict[Tuple[str, float], List[Dict[str, str]]] = {}

    def __init__(self, data_dir: Optional[str] = None) -> None:
        """Initialize the store with an optional explicit data directory."""
        self.data_dir = data_dir or self._resolve_data_dir()
        logger.info("%s initialized with data_dir: %s", self.__class__.__name__, self.data_dir)

    def _resolve_data_dir(self) -> str:
        """Resolve the data directory from the environment or the repository layout."""
        configured = os.environ.get("INVENTORY_DATA_DIR")
        if configured and os.path.isdir(configured):
            return os.path.abspath(configured)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.abspath(os.path.join(base_dir, "..", "..", "data")),
            os.path.abspath(os.path.join(base_dir, "..", "..", "..", "data")),
            os.path.abspath(os.path.join(os.getcwd(), "data")),
        ]
        return next((path for path in candidates if os.path.isdir(path)), candidates[0])

    def load(self, filename: str) -> List[Dict[str, str]]:
        """Load and cache a CSV file from the data directory."""
        file_path = os.path.join(self.data_dir, filename)
        if not os.path.exists(file_path):
            logger.warning("Data file not found: %s", file_path)
            return []
        modified = os.path.getmtime(file_path)
        cache_key = (file_path, modified)
        if cache_key not in self._cache:
            with open(file_path, mode="r", encoding="utf-8") as handle:
                self._cache[cache_key] = [dict(row) for row in csv.DictReader(handle)]
        return self._cache[cache_key]

    def products(self) -> Dict[str, Dict[str, str]]:
        """Return products keyed by SKU."""
        return {row.get("sku", ""): row for row in self.load("products.csv")}

    def suppliers(self) -> Dict[str, Dict[str, str]]:
        """Return primary suppliers keyed by SKU."""
        return {row.get("sku", ""): row for row in self.load("suppliers.csv")}

    def inventory_rows(self) -> List[Dict[str, str]]:
        """Return raw inventory snapshot rows."""
        return self.load("inventory.csv")

    def inventory_by_sku(self) -> Dict[str, List[Dict[str, str]]]:
        """Group warehouse inventory rows by SKU without losing locations."""
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for row in self.inventory_rows():
            grouped.setdefault(row.get("sku", ""), []).append(row)
        return grouped

    def aggregate_inventory(self) -> Dict[str, Dict[str, float]]:
        """Aggregate physical inventory quantities across all warehouse locations."""
        aggregated: Dict[str, Dict[str, float]] = {}
        for row in self.inventory_rows():
            sku = row.get("sku", "")
            entry = aggregated.setdefault(
                sku,
                {"on_hand": 0.0, "reserved": 0.0, "incoming": 0.0, "unavailable": 0.0},
            )
            entry["on_hand"] += self.to_float(row.get("on_hand_qty"))
            entry["reserved"] += self.to_float(row.get("reserved_qty"))
            entry["incoming"] += self.to_float(row.get("incoming_qty"))
            entry["unavailable"] += self.to_float(row.get("unavailable_qty"))
        return aggregated

    def sales(self) -> List[Dict[str, str]]:
        """Return sales rows."""
        return self.load("sales.csv")

    def open_purchase_orders(self) -> List[Dict[str, str]]:
        """Return purchase orders that are not fully received or cancelled."""
        closed = {"RECEIVED", "CANCELLED", "CLOSED"}
        return [row for row in self.load("purchase_orders.csv") if row.get("status", "").upper() not in closed]

    def location_demand(self) -> List[Dict[str, str]]:
        """Return location-level daily demand."""
        return self.load("location_demand.csv")

    def fulfillment_capacity(self) -> Dict[str, Dict[str, str]]:
        """Return fulfillment capacity keyed by location."""
        return {row.get("location_id", ""): row for row in self.load("fulfillment_capacity.csv")}

    def transfer_lanes(self) -> List[Dict[str, str]]:
        """Return active transfer lanes."""
        return [row for row in self.load("transfer_lanes.csv") if row.get("active", "true").lower() == "true"]

    def transfers(self) -> List[Dict[str, str]]:
        """Return transfer records."""
        return self.load("transfers.csv")

    def customer_orders(self) -> List[Dict[str, str]]:
        """Return customer order lines."""
        return self.load("customer_orders.csv")

    def order_allocations(self) -> List[Dict[str, str]]:
        """Return order allocation records."""
        return self.load("order_allocations.csv")

    def sellability_rules(self) -> List[Dict[str, str]]:
        """Return channel and location sellability rules."""
        return self.load("sellability_rules.csv")

    def inventory_batches(self) -> List[Dict[str, str]]:
        """Return batch-level inventory with quality and expiry state."""
        return self.load("inventory_batches.csv")

    def quality_events(self) -> List[Dict[str, str]]:
        """Return quality, damage, expiry, and recall events."""
        return self.load("quality_events.csv")

    def recalls(self) -> List[Dict[str, str]]:
        """Return recall records."""
        return self.load("recalls.csv")

    def inventory_counts(self) -> List[Dict[str, str]]:
        """Return cycle-count history."""
        return self.load("inventory_counts.csv")

    def freight_costs(self) -> List[Dict[str, str]]:
        """Return actual freight and landed-cost records."""
        return self.load("freight_costs.csv")

    def policy_history(self) -> List[Dict[str, str]]:
        """Return inventory policy history."""
        return self.load("policy_history.csv")

    def inventory_snapshots(self) -> List[Dict[str, str]]:
        """Return dated inventory snapshots."""
        return self.load("inventory_snapshots.csv")

    def approvals(self) -> List[Dict[str, str]]:
        """Return approval tasks."""
        return self.load("approvals.csv")

    def recommendation_history(self) -> List[Dict[str, str]]:
        """Return recommendation decision history."""
        return self.load("recommendation_history.csv")

    def action_log(self) -> List[Dict[str, str]]:
        """Return the seeded action audit log."""
        return self.load("action_log.csv")

    def integration_health(self) -> List[Dict[str, str]]:
        """Return integration freshness and availability records."""
        return self.load("integration_health.csv")

    def reference_time(self) -> datetime.datetime:
        """Return a deterministic reference time derived from the freshest data event."""
        timestamps: List[datetime.datetime] = []
        for row in self.inventory_rows():
            parsed = self.to_datetime(row.get("snapshot_at"))
            if parsed:
                timestamps.append(parsed)
        for row in self.load("sync_status.csv"):
            parsed = self.to_datetime(row.get("last_sync_at"))
            if parsed:
                timestamps.append(parsed)
        for row in self.load("po_events.csv"):
            parsed = self.to_datetime(row.get("occurred_at"))
            if parsed:
                timestamps.append(parsed)
        for filename, field in [
            ("transfers.csv", "created_at"),
            ("quality_events.csv", "detected_at"),
            ("approvals.csv", "requested_at"),
            ("recommendation_history.csv", "generated_at"),
            ("action_log.csv", "requested_at"),
            ("integration_health.csv", "last_attempt_at"),
        ]:
            for row in self.load(filename):
                parsed = self.to_datetime(row.get(field))
                if parsed:
                    timestamps.append(parsed)
        if timestamps:
            return max(timestamps)
        return datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def normalize_optional(value: Any) -> Optional[str]:
        """Treat common "no filter" tokens as an absent optional value."""
        if value is None:
            return None
        text = str(value).strip()
        if text.lower() in {"", "null", "none", "all", "any"}:
            return None
        return text

    @staticmethod
    def to_float(value: Any, default: float = 0.0) -> float:
        """Parse a float without raising on blank or malformed values."""
        try:
            if value is None or str(value).strip() == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def to_int(value: Any, default: int = 0) -> int:
        """Parse an integer without raising on blank or malformed values."""
        try:
            if value is None or str(value).strip() == "":
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def to_datetime(value: Any) -> Optional[datetime.datetime]:
        """Parse an ISO timestamp, assuming the India offset when it is missing."""
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        return parsed
