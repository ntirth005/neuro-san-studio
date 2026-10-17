# Project Summary: Inventory Operations Agent

## The problem

Small and medium e-commerce sellers make inventory decisions with incomplete, conflicting data.
Stock figures disagree between the warehouse, storefront, and marketplace; demand is seasonal or
spiky; supplier lead times vary; purchasing budgets are limited; some stock is damaged, expired,
or recalled; and every recommendation has to survive finance and approval scrutiny.

A spreadsheet can calculate a reorder point, but it cannot reconcile sources, explain uncertainty,
enforce supplier constraints, route returns, protect sellable inventory from recall exposure,
compare financial trade-offs, or keep an audit trail of what the agent did and why.

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

The agents do what LLMs are good at: select the right tool, interpret the result, explain the
trade-off, and cite the evidence. The tools do what LLMs are bad at: arithmetic, state
transitions, policy enforcement, and reproducibility.

## What makes it stand out

1. **Deterministic truth.** Quantities, dates, costs, and confidence intervals are computed in
   Python. The same data produces the same result.
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
8. **Token-efficient delegation.** One specialist hop per question, compact tool outputs, top-N
   issue lists, and no raw CSV rows in prompts.
9. **Human approval is enforced outside the LLM.** Follow-up messages are drafts; PO sends are
   blocked without an approved task and a healthy integration.

## Demo flow

1. Ask for stock visibility: the integrity agent shows one SKU where the storefront claims more
   units than the warehouse, and one marketplace record that is stale.
2. Ask why an SKU is negative: the ledger trace identifies the overselling events.
3. Ask which SKUs will run out in 14 days: the demand agent returns a ranked list with lost-GMV
   exposure and confidence.
4. Ask what to reorder under INR 30,000: the replenishment engine returns a compliant queue and a
   bounded-knapsack allocation.
5. Ask whether an order can be fulfilled from another warehouse: the network agent compares
   transfer cost and timing against a new purchase.
6. Ask which stock is damaged, near expiry, or recalled: the quality agent separates usable and
   unusable units and flags channel exposure.
7. Ask how much cash is tied up and what happens to service levels after a 10% inventory
   reduction: the finance agent shows the basis and the service-level trade-off.
8. Ask what happens if demand rises 20% or a supplier fails: the scenario agent returns shortages,
   purchase needs, and contingency options.
9. Ask for the five most urgent problems today: the operations agent ranks the queue and
   escalates overdue approvals.
10. Ask to create a PO draft and send it: the draft is idempotent, and the send is blocked until
    approval, spend limit, and integration health all pass.

## Impact

The design targets the operational questions that precede and follow a purchase order. It can
reduce manual reconciliation time, expose stockouts earlier, avoid over-ordering against stale
channel data, enforce supplier constraints automatically, prevent unsafe sale of recalled stock,
ground financial decisions in explicit assumptions, and make every recommendation auditable.

## Limitations and next steps

- All data is synthetic; marketplace, email, and ERP integrations are simulated locally.
- Forecasting is a lightweight statistical engine, not a trained ML service.
- Follow-ups are drafted locally and PO sends are simulated; no external message is transmitted.
- Next: live Shopify/Amazon adapters, scheduler-driven alerts, real email/WhatsApp approval
  workflows, and evaluation against a seller's existing process.
