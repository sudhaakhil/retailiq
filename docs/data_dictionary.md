# Data dictionary

*Generated from `retailiq/db/schema.py` by `scripts/generate_docs.py`. All data is SIMULATED. Money is in INR and excludes GST; dates are stored as ISO text (`YYYY-MM-DD`).*

Every table lists its **grain** (what one row represents) and its **keys**. Always aggregate to a common grain before joining two tables.

## Reference data

### `meta`

**Grain:** One row per dataset property (seed, version, period, simulated flag).

**Primary key:** `key`

| Column | Type | Nullable |
|---|---|---|
| `key` | VARCHAR(50) | no |
| `value` | TEXT | no |

### `stores`

**Grain:** One row per store.

**Primary key:** `store_id`

| Column | Type | Nullable |
|---|---|---|
| `store_id` | VARCHAR(10) | no |
| `store_name` | VARCHAR(80) | no |
| `city` | VARCHAR(50) | no |
| `opened_date` | VARCHAR(10) | no |
| `size_sqft` | INTEGER | no |

### `suppliers`

**Grain:** One row per supplier. lead_time_days is the quoted (nominal) lead time.

**Primary key:** `supplier_id`

| Column | Type | Nullable |
|---|---|---|
| `supplier_id` | VARCHAR(10) | no |
| `supplier_name` | VARCHAR(80) | no |
| `lead_time_days` | INTEGER | no |
| `on_time_rate` | FLOAT | no |
| `payment_terms_days` | INTEGER | no |

### `products`

**Grain:** One row per product (SKU). unit_price is the list price before discounts.

**Primary key:** `product_id`

**Foreign keys:** `supplier_id` → `suppliers.supplier_id`

| Column | Type | Nullable |
|---|---|---|
| `product_id` | VARCHAR(10) | no |
| `sku` | VARCHAR(20) | no |
| `product_name` | VARCHAR(80) | no |
| `category` | VARCHAR(40) | no |
| `supplier_id` | VARCHAR(10) | no |
| `unit_price` | FLOAT | no |
| `initial_unit_cost` | FLOAT | no |
| `case_pack` | INTEGER | no |
| `max_units_per_order` | INTEGER | no |

### `campaigns`

**Grain:** One row per campaign. store_id/target_category NULL means all stores/categories.

**Primary key:** `campaign_id`

**Foreign keys:** `store_id` → `stores.store_id`

| Column | Type | Nullable |
|---|---|---|
| `campaign_id` | VARCHAR(10) | no |
| `campaign_name` | VARCHAR(80) | no |
| `channel` | VARCHAR(30) | no |
| `objective` | VARCHAR(20) | no |
| `start_date` | VARCHAR(10) | no |
| `end_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | yes |
| `target_category` | VARCHAR(40) | yes |
| `discount_pct` | FLOAT | no |
| `planned_budget` | FLOAT | no |
| `status` | VARCHAR(12) | no |

### `customers`

**Grain:** One row per registered (loyalty or business) customer.

**Primary key:** `customer_id`

**Foreign keys:** `home_store_id` → `stores.store_id`; `acquisition_campaign_id` → `campaigns.campaign_id`

| Column | Type | Nullable |
|---|---|---|
| `customer_id` | VARCHAR(10) | no |
| `customer_name` | VARCHAR(80) | no |
| `customer_type` | VARCHAR(10) | no |
| `home_store_id` | VARCHAR(10) | no |
| `registration_date` | VARCHAR(10) | no |
| `acquisition_channel` | VARCHAR(30) | no |
| `acquisition_campaign_id` | VARCHAR(10) | yes |
| `credit_terms_days` | INTEGER | no |

## Sales and collections

### `sales_transactions`

**Grain:** One row per sales transaction (basket). Amounts exclude GST. customer_id NULL = anonymous walk-in. campaign_id = single attributed campaign (or NULL).

**Primary key:** `transaction_id`

**Foreign keys:** `store_id` → `stores.store_id`; `campaign_id` → `campaigns.campaign_id`; `customer_id` → `customers.customer_id`

| Column | Type | Nullable |
|---|---|---|
| `transaction_id` | VARCHAR(12) | no |
| `transaction_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `customer_id` | VARCHAR(10) | yes |
| `channel` | VARCHAR(12) | no |
| `payment_method` | VARCHAR(10) | no |
| `campaign_id` | VARCHAR(10) | yes |
| `gross_amount` | FLOAT | no |
| `discount_amount` | FLOAT | no |
| `net_amount` | FLOAT | no |
| `due_date` | VARCHAR(10) | no |

### `sales_lines`

**Grain:** One row per product within a transaction. unit_cost = weighted-average cost at sale time.

**Primary key:** `line_id`

**Foreign keys:** `transaction_id` → `sales_transactions.transaction_id`; `product_id` → `products.product_id`

| Column | Type | Nullable |
|---|---|---|
| `line_id` | VARCHAR(14) | no |
| `transaction_id` | VARCHAR(12) | no |
| `product_id` | VARCHAR(10) | no |
| `quantity` | INTEGER | no |
| `unit_price` | FLOAT | no |
| `discount_amount` | FLOAT | no |
| `net_amount` | FLOAT | no |
| `unit_cost` | FLOAT | no |
| `cogs_amount` | FLOAT | no |

### `returns`

**Grain:** One row per returned sales line (full-line returns). Refund paid on return_date.

**Primary key:** `return_id`

**Foreign keys:** `store_id` → `stores.store_id`; `product_id` → `products.product_id`; `transaction_id` → `sales_transactions.transaction_id`; `line_id` → `sales_lines.line_id`

| Column | Type | Nullable |
|---|---|---|
| `return_id` | VARCHAR(12) | no |
| `line_id` | VARCHAR(14) | no |
| `transaction_id` | VARCHAR(12) | no |
| `return_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `product_id` | VARCHAR(10) | no |
| `quantity` | INTEGER | no |
| `refund_amount` | FLOAT | no |
| `cogs_reversal` | FLOAT | no |
| `reason` | VARCHAR(30) | no |

### `opening_receivables`

**Grain:** One row per business-customer invoice outstanding at the opening date.

**Primary key:** `opening_receivable_id`

**Foreign keys:** `customer_id` → `customers.customer_id`; `store_id` → `stores.store_id`

| Column | Type | Nullable |
|---|---|---|
| `opening_receivable_id` | VARCHAR(10) | no |
| `customer_id` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `invoice_date` | VARCHAR(10) | no |
| `due_date` | VARCHAR(10) | no |
| `amount` | FLOAT | no |

### `customer_receipts`

**Grain:** One row per cash collection (settlement) of a transaction or opening receivable.

**Primary key:** `receipt_id`

**Foreign keys:** `opening_receivable_id` → `opening_receivables.opening_receivable_id`; `transaction_id` → `sales_transactions.transaction_id`; `customer_id` → `customers.customer_id`; `store_id` → `stores.store_id`

| Column | Type | Nullable |
|---|---|---|
| `receipt_id` | VARCHAR(12) | no |
| `receipt_date` | VARCHAR(10) | no |
| `transaction_id` | VARCHAR(12) | yes |
| `opening_receivable_id` | VARCHAR(10) | yes |
| `customer_id` | VARCHAR(10) | yes |
| `store_id` | VARCHAR(10) | no |
| `payment_method` | VARCHAR(10) | no |
| `amount` | FLOAT | no |

## Procurement and stock

### `purchase_orders`

**Grain:** One row per purchase order to one supplier for one store.

**Primary key:** `po_id`

**Foreign keys:** `supplier_id` → `suppliers.supplier_id`; `store_id` → `stores.store_id`

| Column | Type | Nullable |
|---|---|---|
| `po_id` | VARCHAR(12) | no |
| `supplier_id` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `order_date` | VARCHAR(10) | no |
| `expected_date` | VARCHAR(10) | no |
| `received_date` | VARCHAR(10) | yes |
| `status` | VARCHAR(10) | no |
| `total_cost` | FLOAT | no |

### `purchase_order_lines`

**Grain:** One row per product on a purchase order.

**Primary key:** `po_line_id`

**Foreign keys:** `po_id` → `purchase_orders.po_id`; `product_id` → `products.product_id`

| Column | Type | Nullable |
|---|---|---|
| `po_line_id` | VARCHAR(14) | no |
| `po_id` | VARCHAR(12) | no |
| `product_id` | VARCHAR(10) | no |
| `quantity_ordered` | INTEGER | no |
| `quantity_received` | INTEGER | no |
| `unit_cost` | FLOAT | no |
| `line_cost` | FLOAT | no |

### `supplier_invoices`

**Grain:** One row per supplier invoice (raised when goods are received; opening invoices flagged).

**Primary key:** `invoice_id`

**Foreign keys:** `supplier_id` → `suppliers.supplier_id`; `po_id` → `purchase_orders.po_id`

| Column | Type | Nullable |
|---|---|---|
| `invoice_id` | VARCHAR(12) | no |
| `supplier_id` | VARCHAR(10) | no |
| `po_id` | VARCHAR(12) | yes |
| `invoice_date` | VARCHAR(10) | no |
| `due_date` | VARCHAR(10) | no |
| `amount` | FLOAT | no |
| `is_opening` | INTEGER | no |

### `supplier_payments`

**Grain:** One row per payment to a supplier against one invoice.

**Primary key:** `payment_id`

**Foreign keys:** `supplier_id` → `suppliers.supplier_id`; `invoice_id` → `supplier_invoices.invoice_id`

| Column | Type | Nullable |
|---|---|---|
| `payment_id` | VARCHAR(12) | no |
| `invoice_id` | VARCHAR(12) | no |
| `supplier_id` | VARCHAR(10) | no |
| `payment_date` | VARCHAR(10) | no |
| `amount` | FLOAT | no |

### `stock_movements`

**Grain:** One row per stock movement of one product at one store.

**Primary key:** `movement_id`

**Foreign keys:** `product_id` → `products.product_id`; `store_id` → `stores.store_id`

| Column | Type | Nullable |
|---|---|---|
| `movement_id` | VARCHAR(14) | no |
| `movement_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `product_id` | VARCHAR(10) | no |
| `movement_type` | VARCHAR(10) | no |
| `quantity` | INTEGER | no |
| `unit_cost` | FLOAT | no |
| `value` | FLOAT | no |
| `reference_id` | VARCHAR(14) | yes |

### `inventory_daily`

**Grain:** One row per store x product x day (end-of-day balance at weighted-average cost).

**Primary key:** `balance_date`, `store_id`, `product_id`

**Foreign keys:** `store_id` → `stores.store_id`; `product_id` → `products.product_id`

| Column | Type | Nullable |
|---|---|---|
| `balance_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `product_id` | VARCHAR(10) | no |
| `quantity_on_hand` | INTEGER | no |
| `inventory_value` | FLOAT | no |
| `avg_unit_cost` | FLOAT | no |
| `quantity_on_order` | INTEGER | no |

## Costs, assets and financing

### `operating_expenses`

**Grain:** One row per expense. Recognised and paid on expense_date (no accruals). store_id NULL = shared head-office cost. Excludes depreciation (derived from capex).

**Primary key:** `expense_id`

**Foreign keys:** `store_id` → `stores.store_id`; `campaign_id` → `campaigns.campaign_id`

| Column | Type | Nullable |
|---|---|---|
| `expense_id` | VARCHAR(12) | no |
| `expense_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | yes |
| `category` | VARCHAR(40) | no |
| `cost_behaviour` | VARCHAR(10) | no |
| `campaign_id` | VARCHAR(10) | yes |
| `description` | VARCHAR(120) | no |
| `amount` | FLOAT | no |

### `capital_expenditures`

**Grain:** One row per fixed-asset purchase (incl. assets bought before the opening date).

**Primary key:** `capex_id`

**Foreign keys:** `store_id` → `stores.store_id`

| Column | Type | Nullable |
|---|---|---|
| `capex_id` | VARCHAR(10) | no |
| `capex_date` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | yes |
| `description` | VARCHAR(120) | no |
| `amount` | FLOAT | no |
| `useful_life_months` | INTEGER | no |

### `loans`

**Grain:** One row per term loan (equal monthly instalments).

**Primary key:** `loan_id`

| Column | Type | Nullable |
|---|---|---|
| `loan_id` | VARCHAR(10) | no |
| `lender` | VARCHAR(80) | no |
| `start_date` | VARCHAR(10) | no |
| `principal` | FLOAT | no |
| `annual_rate` | FLOAT | no |
| `term_months` | INTEGER | no |
| `payment_day` | INTEGER | no |

### `loan_payments`

**Grain:** One row per loan instalment paid within the simulated period.

**Primary key:** `loan_payment_id`

**Foreign keys:** `loan_id` → `loans.loan_id`

| Column | Type | Nullable |
|---|---|---|
| `loan_payment_id` | VARCHAR(12) | no |
| `loan_id` | VARCHAR(10) | no |
| `payment_date` | VARCHAR(10) | no |
| `instalment_no` | INTEGER | no |
| `interest_amount` | FLOAT | no |
| `principal_amount` | FLOAT | no |
| `balance_after` | FLOAT | no |

### `equity_transactions`

**Grain:** One row per owner investment or owner drawing.

**Primary key:** `equity_txn_id`

| Column | Type | Nullable |
|---|---|---|
| `equity_txn_id` | VARCHAR(10) | no |
| `txn_date` | VARCHAR(10) | no |
| `txn_type` | VARCHAR(20) | no |
| `amount` | FLOAT | no |
| `description` | VARCHAR(120) | no |

### `opening_balances`

**Grain:** One row per balance-sheet item at the close of the day before the first simulated day.

**Primary key:** `item`

| Column | Type | Nullable |
|---|---|---|
| `item` | VARCHAR(40) | no |
| `as_of_date` | VARCHAR(10) | no |
| `amount` | FLOAT | no |
| `note` | VARCHAR(200) | no |

### `budgets`

**Grain:** One row per month x store (or HQ) x budget line.

**Primary key:** `month`, `store_id`, `line_item`

| Column | Type | Nullable |
|---|---|---|
| `month` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `line_item` | VARCHAR(30) | no |
| `amount` | FLOAT | no |

### `cash_ledger`

**Grain:** One row per cash movement, derived from the source tables listed in source_table.

**Primary key:** `cash_txn_id`

| Column | Type | Nullable |
|---|---|---|
| `cash_txn_id` | VARCHAR(14) | no |
| `txn_date` | VARCHAR(10) | no |
| `activity` | VARCHAR(10) | no |
| `category` | VARCHAR(40) | no |
| `amount` | FLOAT | no |
| `source_table` | VARCHAR(30) | no |
| `source_id` | VARCHAR(14) | no |
| `store_id` | VARCHAR(10) | yes |

### `planned_cash_items`

**Grain:** One row per known future cash commitment (planning assumption, editable in the UI).

**Primary key:** `planned_item_id`

| Column | Type | Nullable |
|---|---|---|
| `planned_item_id` | VARCHAR(10) | no |
| `planned_date` | VARCHAR(10) | no |
| `category` | VARCHAR(40) | no |
| `description` | VARCHAR(120) | no |
| `amount` | FLOAT | no |
| `activity` | VARCHAR(10) | no |

## Marketing and feedback

### `campaign_daily`

**Grain:** One row per campaign x day of delivered media.

**Primary key:** `campaign_id`, `activity_date`

**Foreign keys:** `campaign_id` → `campaigns.campaign_id`

| Column | Type | Nullable |
|---|---|---|
| `campaign_id` | VARCHAR(10) | no |
| `activity_date` | VARCHAR(10) | no |
| `spend` | FLOAT | no |
| `impressions` | INTEGER | no |
| `clicks` | INTEGER | no |

### `reviews`

**Grain:** One row per customer review of one purchased product.

**Primary key:** `review_id`

**Foreign keys:** `transaction_id` → `sales_transactions.transaction_id`; `product_id` → `products.product_id`; `store_id` → `stores.store_id`; `customer_id` → `customers.customer_id`

| Column | Type | Nullable |
|---|---|---|
| `review_id` | VARCHAR(10) | no |
| `review_date` | VARCHAR(10) | no |
| `customer_id` | VARCHAR(10) | no |
| `product_id` | VARCHAR(10) | no |
| `store_id` | VARCHAR(10) | no |
| `transaction_id` | VARCHAR(12) | no |
| `rating` | INTEGER | no |
| `review_text` | TEXT | no |
