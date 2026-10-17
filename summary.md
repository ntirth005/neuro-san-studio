# Project Summary: Inventory Operations Agent

## The problem

Small and medium e-commerce sellers make inventory decisions with incomplete, conflicting data.
Stock figures disagree between the warehouse, storefront, and marketplace; demand is seasonal or
spiky; supplier lead times vary; purchasing budgets are limited; some stock is damaged, expired,
or recalled; and every recommendation has to survive finance and approval scrutiny. The initial
target is sellers with frequent supplier and inventory exceptions, not enterprises with a fully
integrated ERP.

A spreadsheet alone can calculate a reorder point, but it cannot reconcile sources, explain
uncertainty, enforce supplier constraints, route returns, protect sellable inventory from recall
exposure, compare financial trade-offs, or keep an audit trail of what the agent did and why.

## What we built

An **11-agent, 19-tool inventory operations network** built on Cognizant Neuro SAN Studio. The
front man, `Inventory_Lead`, routes questions to ten specialists:

- `Demand_Analyst` forecasts demand and flags stockout horizons.
- `Inventory_Integrity_Analyst` reconciles warehouse, storefront, and marketplace records.
- `Supplier_Specialist` enforces MOQs, case packs, lead-time variance, and vendor reliability.
- `Capital_Risk_Auditor` plans replenishment, allocates budgets, and explains financial impact.
- `Supplier_Coordination` manages confirmations, delays, short receipts, and alternates.
- `Network_Fulfillment_Analyst` handles transfers, location risk, channel sellability,
  allocation conflicts, redistribution, and closure planning.
- `Quality_Control_Analyst` handles variances, cycle counts, damage, expiry, returns, quality
  signals, and recalls.
- `Operations_Controller` runs daily exceptions, approvals, integration health, PO drafts,
  approval-gated sends, reversals, and audit status.
- `Portfolio_Strategist` performs ABC/XYZ segmentation.
- `Scenario_Simulator` stress-tests demand, lead time, budget, discontinuation, wholesale,
  seasonal, service-cost, and supplier-failure scenarios.

The network answers **80 questions across nine domains**:

1. Inventory visibility and integrity (INV-01–08)
2. Demand forecasting and uncertainty (DEM-01–08)
3. Stockouts, excess, and replenishment (REP-01–10)
4. Supplier coordination and PO lifecycle (SUP-01–12)
5. Multi-location inventory and order fulfillment (LOC-01–08)
6. Inventory anomalies, quality, expiry, and returns (QUA-01–08)
7. Financial and business decisions (FIN-01–08)
8. Scenarios, planning, and decision support (SIM-01–08)
9. Integrations, daily operations, and accountability (OPS-01–10)

## How the agentic system works

The LLM is not the calculator. Every numeric result comes from a deterministic Python CodedTool:

- `InventoryReconciliationTool` compares sources, traces ledger events, detects stale or missing
  imports, and scores trust.
- `DemandForecastTool` selects a forecast model per SKU, returns 80/95% intervals, backtests
  against seasonal naive, explains demand drivers, classifies spikes, and handles new products
  with a labelled fallback.
- `ReplenishmentEngineTool` computes forecast-based quantities, order-by dates, excess and dead
  stock, reduction trade-offs, a bounded-knapsack budget allocation, order frequency, MOQ
  alternatives, and an automation verdict.
- `PurchaseOrderLifecycleTool` and `SupplierSourcingTool` track acknowledgements, confirmed
  dates, partial receipts, price changes, alternate suppliers, and supplier failure scenarios.
- `NetworkFulfillmentTool` compares transfers with purchases, ranks location runout risk, finds
  excess-to-shortage transfers, reconciles in-transit records, checks channel sellability,
  detects allocation conflicts, simulates redistribution, and models a fulfillment-center closure.
- `InventoryQualityTool` explains variances, prioritizes cycle counts, separates usable and
  unusable batches, models expiry waste, routes returns, detects recurring discrepancies, groups
  quality signals, and checks recall exposure.
- `InventoryFinanceTool` calculates capital, gross and net profit, carrying cost, stockout impact,
  budget-versus-hold, supplier landed cost, service-level scenarios, and policy savings with
  explicit assumptions.
- `PlanningScenarioTool` simulates demand changes, lead-time shocks, budget cuts,
  discontinuations, wholesale orders, seasonal plans, service-versus-cost trade-offs, and
  supplier failure.
- `OperationsControlTool` builds the daily exception queue, summarizes changes, tracks approvals,
  explains recommendation changes, checks integration health, creates idempotent PO drafts, gates
  PO sends, creates compensating reversals, explains recommendations, and reports action status.

Supporting tools cover baseline positions and velocity (`InventoryAnalyticsTool`), return/RTO
offsets (`ReturnPipelineTool`), ABC/XYZ segmentation (`ABCClassificationTool`), supplier
constraints and reliability (`SupplierConstraintsTool`, `SupplierReliabilityTool`), budget
optimization (`KnapsackBudgetOptimizerTool`), dead-stock liquidation
(`DeadStockLiquidationTool`), PO drafting (`PurchaseOrderDraftTool`), and scenario stress tests
(`ScenarioSimulationTool`).

The agents do what LLMs are good at: select the right tool, interpret the result, explain the
trade-off, and cite the evidence. The tools do what LLMs are bad at: arithmetic, state
transitions, policy enforcement, and reproducibility.

## What makes it stand out

1. **Deterministic truth.** Quantities, dates, costs, and confidence intervals are computed in
   Python. The same data and tool inputs produce the same numeric result.
2. **Uncertainty is visible.** Forecasts include intervals, confidence tiers, and backtest error
   instead of a single falsely precise number.
3. **Conflicts are surfaced.** The integrity agent reports stale sources, negative inventory, and
   channel/warehouse mismatches rather than averaging them away.
4. **Quality is a first-class constraint.** Positive stock is never assumed sellable. Batch
   quality, expiry, recall, reservation, and channel eligibility are checked before a sale.
5. **Financial reasoning is grounded.** Every finance answer shows its basis, assumptions, and
   uncertainty; gross profit is kept separate from net profit.
6. **The lifecycle continues after the PO.** The system tracks confirmations, delays, short
   receipts, price changes, approvals, sends, reversals, and audit status.
7. **Operations are accountable.** Daily exceptions are ranked, approvals escalate by SLA, and
   every action is reported as completed, failed, pending, or blocked.
8. **Token-efficient delegation.** One specialist hop per question wherever possible, compact tool
   outputs, top-N issue lists, and no raw CSV rows in prompts.
9. **Human approval is enforced outside the LLM.** Follow-up messages are drafts; PO sends are
   blocked without an approved task and a healthy integration.

## Demo flow

1. Reconcile warehouse, storefront, and marketplace stock, then trace a negative SKU.
2. Forecast 14-day runout and build a reorder queue under INR 30,000.
3. Compare a warehouse transfer with a new purchase for a customer order.
4. Separate damaged, quarantined, expired, and recalled stock.
5. Show cash tied up and the service-level impact of a 10% inventory reduction.
6. Simulate demand rising 20% and a complete supplier failure.
7. Rank the daily exception queue and create/send a PO through the approval gate.

## Validation

- **86 unit and data-contract tests pass** across 12 modules.
- HOCON validation passes for `registries/inventory_ops.hocon`.
- **80 questions across nine domains** are mapped in `docs/inventory_ops_question_coverage.md`.
- Live smoke tests cover location, quality, finance, scenario, operations, and PO-send guardrails.
- The demo uses deterministic synthetic CSV data, so judges can reproduce the same results.

## Impact

The design targets the operational questions that precede and follow a purchase order. It can
reduce manual reconciliation time, expose stockouts earlier, avoid over-ordering against stale
channel data, enforce supplier constraints automatically, prevent unsafe sale of recalled stock,
ground financial decisions in explicit assumptions, and make every recommendation auditable.

Commercially, the path is subscription software priced per store or per operations seat: land on
supplier exception handling, then expand into end-to-end inventory operations.

## Limitations and next steps

- All data is synthetic; marketplace, email, and ERP integrations are simulated locally.
- Forecasting is a lightweight statistical engine, not a trained ML service.
- Follow-ups are drafted locally and PO sends are simulated; no external message is transmitted.
- Next: live Shopify/Amazon adapters, scheduler-driven alerts, real email/WhatsApp approval
  workflows, and evaluation against a seller's existing process.

## Repository references

- `architecture.md` — judge-facing architecture description.
- `architecture_v2.md` — technical architecture with Mermaid/UML diagrams.
- `docs/inventory_ops_question_coverage.md` — all 80 questions mapped to agents and tools.
- `docs/known_limitations.md` — detailed boundary audit and unsolved edge cases.
- `README.md` — setup, run, validate, and test instructions.
- `requirements.txt` — Python dependencies.
