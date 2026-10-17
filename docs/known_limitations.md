# Known Limitations and Unsolved Edge Cases

This document is the honest boundary of the current MVP. The network is strong at
decision support, scenario analysis, and bounded deterministic optimization. It is not an
autonomous execution engine for a production-scale physical supply chain.

The limitations below are deliberate scope boundaries, not hidden behavior.

## 1. Algorithmic and mathematical blind spots

### A. Multi-echelon inventory optimization

The fulfillment engine checks point-to-point transfer candidates between locations. It does not
solve a joint multi-echelon stochastic optimization across a central warehouse, regional hubs, and
downstream stores. It treats each location as an independent node instead of computing the
system-wide holding and service trade-off.

**Failure mode:** unnecessary transfers between regional nodes when one upstream buffer would
have absorbed the demand shock at lower total cost.

### B. Joint multi-constraint optimization

The knapsack optimizer ranks SKUs under a single financial budget ceiling. It does not jointly
solve:

- working-capital limits;
- warehouse volume or cubic-capacity limits;
- container weight limits;
- pallet-layer or supplier palletization tiers;
- multi-SKU freight brackets.

**Failure mode:** an order that fits the INR budget but exceeds the receiving warehouse's physical
capacity or the supplier's pallet configuration.

### C. Intermittent-demand bias

The demand engine uses a lightweight Croston-style method for intermittent demand. It does not
implement the Syntetos-Boylan Approximation or TSB correction. Standard Croston has a known
positive bias for slow-moving, erratic items.

**Failure mode:** over-ordering slow-moving SKUs over a multi-month horizon.

## 2. Physical warehouse and reverse-logistics realities

### A. Residual shelf-life SLAs

Quality checks compare expiry dates with the reference date. They do not enforce retailer-specific
remaining-shelf-life rules, such as rejecting a delivery that has less than 60-70% of its total
shelf life left even when it has not expired.

**Failure mode:** a batch is treated as sellable, then rejected at the retailer's dock, causing
double freight and penalty chargebacks.

### B. Reverse-logistics grading latency

The return pipeline counts in-transit RTO units and can offset them against replenishment. It does
not model courier transit time, unpacking, sanitization, grading, missing accessories, or the
portion of returns that must be marked down rather than restocked at full price.

**Failure mode:** a reorder is delayed for in-transit returns, but the returned units arrive late or
partly unsellable, creating an avoidable stockout.

### C. Supplier minimum order value and freight step-functions

The supplier engine validates SKU-level MOQs and case packs. It does not enforce supplier-level
minimum order values across an assortment, LTL versus FTL freight brackets, or factory-run
minimums.

**Failure mode:** a PO satisfies each SKU MOQ but is rejected by the supplier because the total
invoice is below the vendor's minimum factory-run value.

## 3. Financial, tax, and cross-border blind spots

| Dimension | Production reality | Current MVP assumption | Consequence |
| --- | --- | --- | --- |
| GST / input tax credit | Procurement tax is offset against output tax, with timing differences | Flat landed cost | Cash-burn estimates omit tax timing |
| FX volatility | Overseas suppliers invoice in USD or RMB over long lead times | INR-only costs | Margin is exposed to currency movement |
| Early-payment discounts | Suppliers offer terms such as 2/10 net 30 | Static payment terms | Missed working-capital return |

## 4. Integration and execution architecture

### A. Concurrency and race conditions

The data layer reads local CSV snapshots and keeps an in-memory cache. It does not provide
row-level locks, distributed locks, or ACID transactions.

**Failure mode:** concurrent reads can produce stale decisions, and two workflows can allocate the
same physical unit unless an external system enforces the final reservation.

### B. External side-effect isolation

PO sends and supplier messages are simulated locally. The tool deliberately reports
`external_side_effect: false` and `simulated: true`. It does not handle live API rate limits,
gateway timeouts, idempotency-key expiry, webhook validation, or OAuth refresh failures.

**Failure mode:** the workflow is safe for a demo, but it is not a production email, marketplace,
ERP, or WMS connector.

## 5. Multi-agent orchestration and latency

A front-man request that fans out to three specialists incurs multiple LLM hops before the final
synthesis. That is acceptable for planning and exception review, but it is not suitable for
real-time checkout reservations or sub-200 ms allocation decisions.

Two further limits:

- numbers pass through multiple LLM layers, so the final prose can transpose or round a value even
  when the deterministic tool is exact;
- demand and finance disagreements are resolved through prompt arbitration, not a formal
  Pareto-frontier or constrained-optimization model.

## 6. Summary matrix

| Problem domain | Supported today? | Limitation |
| --- | :---: | --- |
| Deterministic data reconciliation | Yes | Ledger, negative stock, and freshness checks are deterministic |
| Uncensored demand and outlier filtering | Yes | Stockout-day correction and B2B spike filtering are implemented |
| Vendor scorecards and lead-time jitter | Yes | Safety stock includes supplier variance |
| Dead-stock liquidation analysis | Yes | Trapped capital and markdown lift are modeled |
| Multi-echelon network balancing | No | Point-to-point transfers only |
| Simultaneous physical constraints | No | Budget-only knapsack; no CBM, weight, or pallet limits |
| Live ERP/WMS transactional locking | No | CSV snapshots and in-memory cache; no ACID or distributed locks |
| Physical return inspection and grading | No | In-transit returns are not treated as automatically sellable |

## 7. What this means for the demo

The demo should claim workflow completion inside the simulated boundary:

- investigate an exception;
- quantify inventory and financial impact;
- draft a supplier action;
- enforce approval before a send;
- record the outcome in an audit trail.

It should not claim production-grade multi-echelon optimization, live transactional execution, or
fully automated supplier messaging. Those are the next engineering milestones, and they require
real seller data, integration adapters, and customer validation.
