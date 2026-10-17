# Synthetic Inventory Operations Data

All records in this folder are synthetic. There is no personal, customer, supplier-confidential,
or financial data from any real organization.

## Baseline files

| File | Purpose |
|---|---|
| `products.csv` | Product catalog, costs, prices, categories |
| `inventory.csv` | Warehouse inventory snapshot by location |
| `channel_inventory.csv` | Storefront and marketplace available quantities |
| `inventory_locations.csv` | Location types, channels, and overlap groups |
| `inventory_ledger.csv` | Order, return, transfer, adjustment, and sync events |
| `sync_status.csv` | Last sync time and expected cadence per location |
| `sales.csv` | 180 days of daily sales lines |
| `calendar.csv` | Daily calendar context |
| `promotions.csv` | Promotion windows and expected uplift |
| `returns.csv` | Return and RTO records |
| `suppliers.csv` | Primary supplier terms and lead-time statistics |
| `supplier_alternatives.csv` | Approved alternate suppliers |
| `supplier_contacts.csv` | Synthetic contact channels |
| `purchase_orders.csv` | Purchase-order lifecycle records |
| `po_events.csv` | Append-only PO event trail |
| `shipments.csv` | Supplier shipment records |
| `receipts.csv` | Warehouse receipt records |
| `replenishment_policy.csv` | Service level, review period, excess thresholds |
| `cost_policy.csv` | Holding, ordering, shortage, and freight costs |
| `followup_policy.csv` | Acknowledgement and escalation SLAs |

## Extended files

| File | Purpose |
|---|---|
| `location_demand.csv` | 90 days of demand by location, SKU, channel, and region |
| `fulfillment_capacity.csv` | Daily unit capacity, cost, and cutoff by location |
| `transfer_lanes.csv` | Transfer lead time, cost per unit, and maximum quantity |
| `transfers.csv` | Planned, in-transit, delayed, and received transfers |
| `customer_orders.csv` | Open customer order lines and promised dates |
| `order_allocations.csv` | Location allocations and conflict reasons |
| `sellability_rules.csv` | Channel and location sale eligibility rules |
| `inventory_batches.csv` | Batch quantity, expiry, quality status, and recall link |
| `quality_events.csv` | Damage, quarantine, expiry, recall, and quality-reject events |
| `recalls.csv` | Active recall records and affected channels |
| `inventory_counts.csv` | Cycle-count history and variances |
| `freight_costs.csv` | Actual freight, duties, handling, and shortage penalties |
| `policy_history.csv` | Baseline and current inventory policy assumptions |
| `inventory_snapshots.csv` | Yesterday and today positions for change analysis |
| `approvals.csv` | Pending, approved, and rejected tasks with SLA |
| `recommendation_history.csv` | Recommendation versions, evidence, and change reasons |
| `action_log.csv` | Seeded completed, failed, pending, and blocked actions |
| `integration_health.csv` | Marketplace, email, ERP, payments, and WMS health |

The extended files are generated deterministically by
`scripts/generate_extended_inventory_data.py`. The script only writes the additive files and does
not modify the baseline dataset.

## Seeded anomalies

The data intentionally contains:

- a stale marketplace sync and a missing marketplace source;
- negative marketplace inventory for `SKU-006`;
- storefront/warehouse quantity mismatches;
- overlapping marketplace and warehouse locations;
- an unconfirmed purchase order, a delayed purchase order, a short shipment, and a partial receipt;
- a supplier price change and a supplier landed-cost outlier;
- a new product (`SKU-006`) with no sales history;
- a delayed transfer and a planned transfer;
- a physical-but-not-sellable channel mapping for `SKU-006`;
- an over-allocated customer order and a recalled-batch allocation;
- damaged, quarantined, expired, and recalled batches;
- a recurring negative variance for `SKU-001`;
- a near-expiry batch for `SKU-005`;
- an overdue approval and an approved PO-send task;
- a superseded recommendation for `SKU-003`;
- a down marketplace API and a stale marketplace location.

These anomalies make the integrity, forecasting, replenishment, fulfillment, quality, finance,
planning, and operations questions meaningful during a demo.
