# Inventory Operations Question Coverage

The network covers **80 questions** across nine domains. Every numeric answer comes from a
deterministic CodedTool; the agent selects the action, explains the result, and cites the evidence.

## Baseline: 38 questions

| ID | Question focus | Agent | Tool action |
|---|---|---|---|
| INV-01 | What is available to sell? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.available_to_sell` |
| INV-02 | Why do sources disagree? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.source_discrepancies` |
| INV-03 | Which records are stale or missing? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.data_quality` |
| INV-04 | Why is a SKU negative? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.trace_sku` |
| INV-05 | What is committed or over-committed? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.committed_stock` |
| INV-06 | Which source should I trust? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.trust_report` |
| INV-07 | Where is the channel/warehouse mismatch? | Inventory_Integrity_Analyst | `InventoryReconciliationTool.source_discrepancies` |
| INV-08 | Give me the full integrity summary. | Inventory_Integrity_Analyst | `InventoryReconciliationTool.summary` |
| DEM-01 | What is the forecast? | Demand_Analyst | `DemandForecastTool.forecast` |
| DEM-02 | Which SKUs run out within N days? | Demand_Analyst | `DemandForecastTool.stockout_horizon` |
| DEM-03 | Why did demand change? | Demand_Analyst | `DemandForecastTool.explain_change` |
| DEM-04 | Is this spike real or a bulk order? | Demand_Analyst | `DemandForecastTool.classify_spike` |
| DEM-05 | What if a promotion runs? | Demand_Analyst | `DemandForecastTool.promotion_scenario` |
| DEM-06 | How do we forecast a new product? | Demand_Analyst | `DemandForecastTool.new_product_forecast` |
| DEM-07 | How accurate is the forecast? | Demand_Analyst | `DemandForecastTool.backtest` |
| DEM-08 | Which forecasts are reliable? | Demand_Analyst | `DemandForecastTool.reliability` |
| REP-01 | What should I reorder now? | Capital_Risk_Auditor | `ReplenishmentEngineTool.queue` |
| REP-02 | How many units should I order? | Capital_Risk_Auditor | `ReplenishmentEngineTool.quantity` |
| REP-03 | By when should I order? | Capital_Risk_Auditor | `ReplenishmentEngineTool.order_by_date` |
| REP-04 | Where is excess stock? | Capital_Risk_Auditor | `ReplenishmentEngineTool.excess` |
| REP-05 | What is dead stock? | Capital_Risk_Auditor | `ReplenishmentEngineTool.dead_stock` |
| REP-06 | Can I reduce this order? | Capital_Risk_Auditor | `ReplenishmentEngineTool.reduce_order` |
| REP-07 | How should I allocate a budget? | Capital_Risk_Auditor | `ReplenishmentEngineTool.budget_allocate` |
| REP-08 | How often should I order? | Capital_Risk_Auditor | `ReplenishmentEngineTool.order_frequency` |
| REP-09 | What are my MOQ alternatives? | Capital_Risk_Auditor | `ReplenishmentEngineTool.moq_alternatives` |
| REP-10 | Can this be automated? | Capital_Risk_Auditor | `ReplenishmentEngineTool.automation_policy` |
| SUP-01 | Which POs are in exception? | Supplier_Coordination | `PurchaseOrderLifecycleTool.exception_queue` |
| SUP-02 | What is the confirmation status? | Supplier_Coordination | `PurchaseOrderLifecycleTool.confirmation_status` |
| SUP-03 | How reliable is the supplier? | Supplier_Coordination | `SupplierSourcingTool.supplier_scorecard` |
| SUP-04 | What if the supplier ships short? | Supplier_Coordination | `SupplierSourcingTool.shortfall_options` |
| SUP-05 | What is the delay impact? | Supplier_Coordination | `SupplierSourcingTool.delay_impact` |
| SUP-06 | Draft a supplier follow-up. | Supplier_Coordination | `PurchaseOrderLifecycleTool.draft_followup` |
| SUP-07 | What does a price change mean? | Supplier_Coordination | `SupplierSourcingTool.price_change_eval` |
| SUP-08 | Who are the alternate suppliers? | Supplier_Coordination | `SupplierSourcingTool.alternate_suppliers` |
| SUP-09 | Reconcile the receipt. | Supplier_Coordination | `PurchaseOrderLifecycleTool.receipt_reconciliation` |
| SUP-10 | What is still backordered? | Supplier_Coordination | `PurchaseOrderLifecycleTool.partial_fulfillment` |
| SUP-11 | What needs escalation? | Supplier_Coordination | `PurchaseOrderLifecycleTool.escalation_queue` |
| SUP-12 | What if the supplier fails? | Supplier_Coordination | `SupplierSourcingTool.supplier_failure_sim` |

## Multi-location inventory and order fulfillment (LOC)

| ID | Question | Agent | Tool action |
|---|---|---|---|
| LOC-01 | Can I fulfil this order from another warehouse instead of buying more stock? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.fulfill_options` |
| LOC-02 | Which locations will run out first? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.location_stockout_risk` |
| LOC-03 | Where is excess stock that could cover a shortage elsewhere? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.transfer_candidates` |
| LOC-04 | How much inventory is in transit, and when should it arrive? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.in_transit` |
| LOC-05 | Is a product available physically but unavailable for online sale? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.sellability_check` |
| LOC-06 | Which customer orders are at risk because stock is allocated incorrectly? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.allocation_conflicts` |
| LOC-07 | Should inventory be distributed differently before a sale? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.redistribute_plan` |
| LOC-08 | What happens if one fulfillment centre temporarily closes? | Network_Fulfillment_Analyst | `NetworkFulfillmentTool.closure_simulation` |

## Inventory anomalies, quality, expiry, and returns (QUA)

| ID | Question | Agent | Tool action |
|---|---|---|---|
| QUA-01 | Why did recorded stock change unexpectedly? | Quality_Control_Analyst | `InventoryQualityTool.explain_variance` |
| QUA-02 | Which products need a physical stock count? | Quality_Control_Analyst | `InventoryQualityTool.cycle_count_priority` |
| QUA-03 | How much stock is damaged, quarantined, or unsellable? | Quality_Control_Analyst | `InventoryQualityTool.unsellable_summary` |
| QUA-04 | Which items are nearing expiry? | Quality_Control_Analyst | `InventoryQualityTool.expiry_risk` |
| QUA-05 | Can returned products be restocked? | Quality_Control_Analyst | `InventoryQualityTool.return_disposition` |
| QUA-06 | Do we have a recurring discrepancy for the same SKU? | Quality_Control_Analyst | `InventoryQualityTool.recurring_discrepancy` |
| QUA-07 | Which returns or adjustments suggest a quality issue? | Quality_Control_Analyst | `InventoryQualityTool.quality_signals` |
| QUA-08 | Are we selling stock that should be recalled or blocked? | Quality_Control_Analyst | `InventoryQualityTool.recall_check` |

## Financial and business decisions (FIN)

| ID | Question | Agent | Tool action |
|---|---|---|---|
| FIN-01 | How much cash is tied up in inventory? | Capital_Risk_Auditor | `InventoryFinanceTool.capital_tied_up` |
| FIN-02 | Which products generate the most and least gross profit? | Capital_Risk_Auditor | `InventoryFinanceTool.gross_profit_ranking` |
| FIN-03 | How much could excess stock be costing us? | Capital_Risk_Auditor | `InventoryFinanceTool.excess_carrying_cost` |
| FIN-04 | What is the financial impact of a stockout? | Capital_Risk_Auditor | `InventoryFinanceTool.stockout_impact` |
| FIN-05 | Should I spend the budget or hold cash? | Capital_Risk_Auditor | `InventoryFinanceTool.budget_vs_hold` |
| FIN-06 | Which suppliers had the greatest impact on inventory cost? | Capital_Risk_Auditor | `InventoryFinanceTool.supplier_cost_impact` |
| FIN-07 | What happens to service levels if inventory is reduced by 10%? | Capital_Risk_Auditor | `InventoryFinanceTool.service_level_simulation` |
| FIN-08 | Did last month's inventory policy save money? | Capital_Risk_Auditor | `InventoryFinanceTool.policy_savings` |

## Scenarios, planning, and decision support (SIM)

| ID | Question | Agent | Tool action |
|---|---|---|---|
| SIM-01 | What happens if demand rises by 20% next month? | Scenario_Simulator | `PlanningScenarioTool.demand_surge` |
| SIM-02 | What happens if the supplier lead time doubles? | Scenario_Simulator | `PlanningScenarioTool.lead_time_double` |
| SIM-03 | Which products get priority if the budget falls by 30%? | Scenario_Simulator | `PlanningScenarioTool.budget_cut` |
| SIM-04 | What if we discontinue our slowest-selling products? | Scenario_Simulator | `PlanningScenarioTool.discontinue_slow` |
| SIM-05 | Can we fulfil a large new wholesale order? | Scenario_Simulator | `PlanningScenarioTool.wholesale_order` |
| SIM-06 | What should we stock before a major seasonal sale? | Scenario_Simulator | `PlanningScenarioTool.seasonal_plan` |
| SIM-07 | How do we trade off service level against inventory cost? | Scenario_Simulator | `PlanningScenarioTool.service_vs_cost` or `service_level_simulation` |
| SIM-08 | What happens if an important supplier fails completely? | Scenario_Simulator | `PlanningScenarioTool.supplier_failure` |

## Integrations, daily operations, and accountability (OPS)

| ID | Question | Agent | Tool action |
|---|---|---|---|
| OPS-01 | What are the five most urgent inventory problems today? | Operations_Controller | `OperationsControlTool.daily_exception_queue` |
| OPS-02 | What changed since yesterday? | Operations_Controller | `OperationsControlTool.change_since_yesterday` |
| OPS-03 | Which tasks have been waiting for approval too long? | Operations_Controller | `OperationsControlTool.pending_approvals` |
| OPS-04 | Why did an earlier recommendation change? | Operations_Controller | `OperationsControlTool.recommendation_history` |
| OPS-05 | What happens if the marketplace API is unavailable? | Operations_Controller | `OperationsControlTool.integration_health` |
| OPS-06 | Can you create a purchase-order draft for the approved products? | Operations_Controller | `OperationsControlTool.create_po_draft` |
| OPS-07 | Can you send the purchase order to the supplier? | Operations_Controller | `OperationsControlTool.send_po` |
| OPS-08 | How do I undo or correct an incorrect inventory action? | Operations_Controller | `OperationsControlTool.reverse_action` |
| OPS-09 | Why did you recommend this action instead of an alternative? | Operations_Controller | `OperationsControlTool.explain_recommendation` |
| OPS-10 | What tasks did you complete, and which ones failed? | Operations_Controller | `OperationsControlTool.action_status` |

## Guardrails

- Positive physical stock is never automatically sellable: batch quality, expiry, recall,
  reservation, and channel eligibility must pass first.
- Transfers, redistributions, and closure responses are plans, not executions.
- Financial answers show the accounting basis, assumptions, and uncertainty.
- `send_po` is simulated and blocked unless approval, spend limit, and integration health pass.
- Reversals create compensating actions and preserve the original audit trail.
- Runtime artifacts are written only under `output/`; source CSVs are immutable.
