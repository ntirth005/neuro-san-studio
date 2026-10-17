# Inventory Operations Agent — Architecture

## 1. What the system does

The Inventory Operations Agent helps small and medium e-commerce sellers answer the operational,
quality, financial, planning, and accountability questions that surround inventory:

1. **Inventory visibility and integrity:** what can actually be sold, which source is stale,
   where records disagree, and why an SKU is negative.
2. **Demand forecasting and uncertainty:** what will sell next week or next month, why demand
   changed, and which forecasts are too weak to automate against.
3. **Stockouts, excess inventory, and replenishment:** what to order, how much, by when, what to
   do with excess stock, and how to allocate a limited budget.
4. **Supplier coordination and purchase-order lifecycle:** which orders are unconfirmed, late, or
   short, what a price change means, and which alternate supplier can help.
5. **Multi-location fulfillment:** transfer versus buy, location runout risk, excess-to-shortage
   transfers, channel sellability, allocation conflicts, redistribution, and closure simulation.
6. **Quality, expiry, and recalls:** variance investigation, cycle counts, damaged and
   quarantined stock, expiry risk, return disposition, recurring discrepancies, quality signals,
   and recall exposure.
7. **Financial decisions:** inventory capital, gross versus net profit, carrying cost, stockout
   impact, budget-versus-hold, supplier landed cost, service-level scenarios, and policy savings.
8. **Planning scenarios:** demand surges, lead-time shocks, budget cuts, discontinuations,
   wholesale orders, seasonal plans, service-versus-cost trade-offs, and supplier failure.
9. **Daily operations and accountability:** exception queues, change summaries, approvals,
   recommendation history, integration health, idempotent PO drafts, approval-gated sends,
   reversals, recommendation explanations, and action status.

The network is built on **Cognizant Neuro SAN Studio** and uses the AAOSA protocol for dynamic
delegation. All arithmetic and state transitions run in deterministic Python tools; the agents
orchestrate, explain, and cite evidence.

## 2. Agent topology

The network has **11 agents** and **19 deterministic tools**.

```
Inventory_Lead
├── Demand_Analyst
├── Inventory_Integrity_Analyst
├── Supplier_Specialist
├── Capital_Risk_Auditor
├── Supplier_Coordination
├── Network_Fulfillment_Analyst
├── Quality_Control_Analyst
├── Operations_Controller
├── Portfolio_Strategist
└── Scenario_Simulator
```

- **Inventory_Lead** is the only front man. It routes by intent and synthesizes the answer.
- **Demand_Analyst** owns forecasts, demand drivers, and stockout horizon.
- **Inventory_Integrity_Analyst** owns reconciliation, freshness, negative balances, and trust.
- **Supplier_Specialist** owns contract terms, MOQ, lead-time variance, and vendor reliability.
- **Capital_Risk_Auditor** owns replenishment, budget allocation, financial reasoning, and
  purchase-order funding proposals.
- **Supplier_Coordination** owns confirmations, delays, short receipts, and alternates.
- **Network_Fulfillment_Analyst** owns multi-location fulfillment, transfers, channel
  sellability, allocation conflicts, redistribution, and closure planning.
- **Quality_Control_Analyst** owns variances, cycle counts, damage, expiry, returns, quality
  signals, and recall exposure.
- **Operations_Controller** owns daily exceptions, approvals, integration health, PO drafts,
  approval-gated sends, reversals, and audit status.
- **Portfolio_Strategist** owns ABC/XYZ segmentation.
- **Scenario_Simulator** owns demand, lead-time, budget, discontinuation, wholesale, seasonal,
  and supplier-failure scenarios.

The registry sets `max_steps: 75` and `max_execution_seconds: 900` so no run is unbounded.

## 3. Deterministic tools

| Tool | Responsibility |
|---|---|
| `InventoryAnalyticsTool` | Multi-location positions, velocity, safety stock, ROP, risk |
| `InventoryReconciliationTool` | Available-to-sell, discrepancies, freshness, ledger, trust |
| `DemandForecastTool` | Forecasts, intervals, drivers, spikes, backtests, reliability |
| `ReplenishmentEngineTool` | Queue, quantities, deadlines, excess, budget, policy |
| `SupplierConstraintsTool` | MOQ, case-pack rounding, line cost |
| `SupplierReliabilityTool` | Vendor scorecards, stochastic safety stock, volume tiers |
| `KnapsackBudgetOptimizerTool` | Budget allocation benchmark |
| `DeadStockLiquidationTool` | Trapped capital and clearance modeling |
| `ScenarioSimulationTool` | Lead-time and demand-shock stress tests |
| `ABCClassificationTool` | ABC/XYZ portfolio segmentation |
| `ReturnPipelineTool` | Return/RTO pipeline and net velocity |
| `SupplierSourcingTool` | Scorecards, shortfalls, delays, price changes, alternates |
| `PurchaseOrderLifecycleTool` | Exceptions, confirmations, follow-up drafts, receipts, backorders, escalations |
| `PurchaseOrderDraftTool` | Auditable PO draft artifacts |
| `NetworkFulfillmentTool` | Transfer versus buy, location risk, in-transit, sellability, allocations, redistribution, closure |
| `InventoryQualityTool` | Variances, counts, unsellable stock, expiry, returns, quality signals, recalls |
| `InventoryFinanceTool` | Capital, gross/net profit, carrying cost, stockout impact, budget versus hold, landed cost, service levels, policy savings |
| `PlanningScenarioTool` | Demand, lead-time, budget, discontinuation, wholesale, seasonal, service-cost, supplier failure |
| `OperationsControlTool` | Daily queue, changes, approvals, integrations, idempotent drafts, gated sends, reversals, audit |

All tools share `InventoryDataStore`, which resolves `INVENTORY_DATA_DIR` (or the repository
`data/` folder), caches CSV reads, and parses timestamps and numbers consistently.

## 4. Data flow

```
CSV data in data/
      |
      v
InventoryDataStore  --->  deterministic tools  --->  structured result
                                                        |
                                                        v
                                                  specialist agent
                                                        |
                                                        v
                                                  Inventory_Lead
                                                        |
                                                        v
                                             explanation + evidence
```

Typical question flow:

1. Front man identifies the domain and delegates.
2. Specialist calls one or two tools with a bounded action.
3. Tool returns numbers, confidence, reasons, and record references.
4. Specialist explains the result without recomputing it.
5. Front man presents the answer and the recommended next step.

## 5. Token and latency design

- **One specialist hop per question** wherever possible.
- **Tool actions return summaries by default.** Queue and issue lists are capped unless the caller
  asks for a specific SKU, PO, or location.
- **No raw data in prompts.** Tools return aggregates, top issues, and references, not CSV rows,
  daily series, or email threads.
- **Deterministic follow-ups.** Follow-up messages are templates; the LLM only personalizes text.
- **Shared cache.** Repeated lookups reuse the same parsed data within a request.
- **Bounded execution.** `max_steps: 75`, `max_execution_seconds: 900`.

## 6. Safety, approvals, and audit

- The LLM never writes to source CSVs and never performs an external side effect.
- `PurchaseOrderLifecycleTool.draft_followup` writes a local draft and returns
  `requires_human_approval: true`; it never sends.
- `OperationsControlTool.create_po_draft` is idempotent. Repeating the same key returns the prior
  audit record instead of creating a duplicate draft.
- `OperationsControlTool.send_po` is simulated and blocked unless an approved send task, spend
  limit, and healthy email integration all pass.
- Reversals create compensating actions and preserve the original audit trail; nothing is deleted.
- Positive physical stock is never treated as sellable when batch quality, expiry, recall,
  reservation, or channel rules fail.
- Financial answers must show the accounting basis, assumptions, and uncertainty; savings are
  estimates, not guarantees.
- All data in the repository is synthetic and contains no PII.

Runtime action records are written under `output/audit/` and purchase-order artifacts under
`output/purchase_orders/`. Both paths are gitignored. Source data remains immutable.

## 7. Data model

The synthetic dataset includes the original baseline files plus additive files for the extended
question set:

- **Baseline:** `products.csv`, `inventory.csv`, `channel_inventory.csv`,
  `inventory_locations.csv`, `inventory_ledger.csv`, `sync_status.csv`, `sales.csv`,
  `calendar.csv`, `promotions.csv`, `returns.csv`, `suppliers.csv`,
  `supplier_alternatives.csv`, `supplier_contacts.csv`, `purchase_orders.csv`, `po_events.csv`,
  `shipments.csv`, `receipts.csv`, `replenishment_policy.csv`, `cost_policy.csv`,
  `followup_policy.csv`.
- **Multi-location:** `location_demand.csv`, `fulfillment_capacity.csv`, `transfer_lanes.csv`,
  `transfers.csv`, `customer_orders.csv`, `order_allocations.csv`, `sellability_rules.csv`.
- **Quality:** `inventory_batches.csv`, `quality_events.csv`, `recalls.csv`,
  `inventory_counts.csv`.
- **Finance:** `freight_costs.csv`, `policy_history.csv`.
- **Operations:** `inventory_snapshots.csv`, `approvals.csv`, `recommendation_history.csv`,
  `action_log.csv`, `integration_health.csv`.

The generator at `scripts/generate_extended_inventory_data.py` is deterministic and writes only
the additive files.

Seeded anomalies include: a stale marketplace source, negative marketplace inventory,
channel/warehouse mismatches, overlapping locations, an unconfirmed PO, a delayed PO, a short
shipment, a price change, a new product with no sales history, a delayed transfer, a
physical-but-not-sellable SKU, an over-allocated order, damaged/quarantined/expired/recalled
batches, a recurring SKU variance, a supplier landed-cost outlier, an overdue approval, a
superseded recommendation, and a down marketplace API.

## 8. Validation

- `python -m neuro_san_studio validate registries/inventory_ops.hocon` passes.
- **84 unit and data-contract tests** pass across 11 test modules.
- **80 questions** across nine domains are mapped to agents and tools in
  `docs/inventory_ops_question_coverage.md`.
- Live smoke tests cover location, quality, finance, scenario, operations, and PO-send guardrails.
- The demo is checked with `python -m unittest` and deterministic CSV data; no live seller data is
  used.

## 9. Limitations

- Data is synthetic; marketplace, email, and ERP integrations are simulated locally.
- Forecasting uses a lightweight deterministic statistical engine, not a trained ML service.
- Supplier follow-ups and PO sends are drafts/simulations; no external message is transmitted.
- The demo does not include a production database, authentication, or tenant isolation.
- Demand history is simulated for 180 days; real sellers must supply their own exports.
- Real-world integration adapters are the next step, not part of this build.

The full boundary audit is in `docs/known_limitations.md`. It covers the gaps this MVP does not
close yet: joint multi-echelon optimization, simultaneous budget/CBM/weight/pallet constraints,
intermittent-demand bias correction, residual-shelf-life rejection rules, physical return grading,
supplier minimum order value and freight brackets, GST/FX/early-payment modeling, ACID transactions,
live API execution, and sub-200 ms checkout allocation.
