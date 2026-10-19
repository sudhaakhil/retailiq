# Architecture, database outline and five-week plan

## 1. Architecture in one page

```
            ┌───────────────────────────── Streamlit UI ──────────────────────────────┐
            │ streamlit_app.py (navigation, sidebar)   app_pages/*.py (13 pages)       │
            │ retailiq/ui: formatting · state · charts · components · cached           │
            └───────────────┬─────────────────────────────────────────────────────────┘
                            │ calls (read-only)
            ┌───────────────▼──────────────── Analytics (pure pandas/NumPy) ──────────┐
            │ dataset ─ finance ─ capital ─ inventory ─ cashflow ─ funding             │
            │ marketing ─ customers ─ reviews ─ forecasting ─ scenarios ─ actions      │
            └───────────────┬─────────────────────────────────────────────────────────┘
                            │ Dataset.load()
            ┌───────────────▼───────────────┐        ┌────────────────────────────────┐
            │ db/ SQLAlchemy + SQLite       │◄───────│ datagen/ seeded daily simulation│
            │ schema.py · database.py       │ writes │ catalog.py (assumptions)        │
            └───────────────────────────────┘        └────────────────────────────────┘
```

**Design principles**

1. **Layers do not leak.** Pages only display; analytics never import Streamlit (so they can be unit-tested); the database layer only reads and writes tables; the generator only produces tables.
2. **One shared read-only `Dataset`.** All tables are loaded once, dates are normalised, and a few derived frames (`lines`, `daily`, depreciation, loan schedules, daily balances) are built once. Streamlit caches it (`st.cache_resource`) and heavy results (`st.cache_data`) keyed by the database file signature + reference date, so a rebuilt database refreshes automatically.
3. **Grain before joins.** Every table documents what one row means. Analytics aggregate to a common grain first (e.g. `daily` = date × store × product) so combining sales, campaigns, customers and reviews cannot double count.
4. **Consistency by construction, proven by tests.** The generator derives the cash ledger, stock balances and budgets from the same events; the app then re-checks them (balance sheet identity, cash rebuilt from source tables, stock movements vs balances).
5. **Transparent rules over black boxes.** Reorder logic, budget prioritisation, sentiment, forecasting features and Action Center rules are short, documented and inspectable. The core app needs no external API.
6. **Assumptions are visible inputs.** Rates, fees, elasticities, thresholds and stress levels are editable controls, never hidden constants.

## 2. Database outline

28 tables in SQLite (full detail in [data_dictionary.md](data_dictionary.md), diagram in [database_diagram.md](database_diagram.md)).

| Area | Tables (grain) |
|---|---|
| Reference | `meta` (dataset properties), `stores`, `suppliers`, `products`, `campaigns`, `customers` |
| Sales & collections | `sales_transactions` (one basket), `sales_lines` (product in a basket), `returns` (returned line), `customer_receipts` (a collection), `opening_receivables` |
| Procurement & stock | `purchase_orders`, `purchase_order_lines`, `supplier_invoices`, `supplier_payments`, `stock_movements` (one movement), `inventory_daily` (store × product × day) |
| Costs, assets, financing | `operating_expenses`, `capital_expenditures`, `loans`, `loan_payments`, `equity_transactions`, `opening_balances`, `budgets` (month × store × line), `cash_ledger` (one cash movement), `planned_cash_items` |
| Marketing & feedback | `campaign_daily` (campaign × day), `reviews` |

Key relationships: a **sale line** belongs to a **transaction**, which has *at most one* **campaign** (attribution) and optionally a **customer**; sale lines and receipts create **stock movements** and **cash** events; **purchase orders** create stock receipts, supplier invoices and (later) supplier payments; the **cash ledger** summarises every cash-moving table.

## 3. Folder and module responsibilities

See the tree in the [README](../README.md#project-layout). Tests mirror the modules: `tests/test_datagen_and_db.py`, `test_finance_capital.py`, `test_inventory_funding_cash.py`, `test_marketing_customers_reviews.py`, `test_forecasting.py`, `test_actions_scenarios_ui.py`.

## 4. Five-week learning plan (how to study and extend the project)

The code is already complete and working; this plan is for **you to understand it well enough to explain and extend it**, one milestone per week. Each week ends with something you can run, demonstrate and explain.

| Week | Milestone | Read and run | You should be able to explain | Stretch exercise |
|---|---|---|---|---|
| 1 | **Setup, connected synthetic data, database** | `datagen/catalog.py`, `generator.py` (the daily loop), `db/schema.py`; run `init-db`, `check`, `info`; open `data/retailiq.db` in a SQLite viewer | What "grain" means; why transaction totals must equal line totals; how the generator keeps stock, cash and payables consistent; what the seed does | Change `COST_SCALE` or a campaign's discount, regenerate with `--reset`, and watch margins change |
| 2 | **Sales, finance and capital dashboards** | `analytics/finance.py`, `capital.py`, `app_pages/overview.py`, `finance.py`, `working_capital.py` | Gross profit vs operating profit; why loan principal is not an expense; what the balance-sheet check proves; the cash-conversion-cycle formula | Add a "profit by channel" table using `ds.lines` |
| 3 | **Inventory, cash forecasting, funding scenarios** | `inventory.py`, `cashflow.py`, `funding.py`, the two pages | Safety stock and reorder point; how a purchase today becomes a cash payment weeks later; interest vs principal in a loan schedule | Add a "payment holiday" option to the term loan |
| 4 | **Marketing, customers, reviews** | `marketing.py`, `customers.py`, `reviews.py`, the three pages | ROAS vs contribution; why attribution is not causation; RFM and cohort retention; limits of a dictionary sentiment method | Add a new review theme and see the effect |
| 5 | **Forecasting, connected scenarios, tests, polish** | `forecasting.py`, `scenarios.py`, `actions.py`, `tests/` | Why shifting lags prevents leakage; chronological validation; each Action Center rule and its limits | Write one new Action Center rule and a test for it |

### Suggested extensions after week 5
PostgreSQL (change the connection string in `db/database.py`; the schema is plain SQLAlchemy Core), user login, scheduled data refresh, a real data import in place of the generator, an optional LLM narrative layered on top of the rule-based findings (the core must keep working without it), Docker, and hosting.

## 5. Decisions worth defending in an interview

* **SQLite + SQLAlchemy** for zero-setup local use, with a schema that ports to PostgreSQL.
* **Simulation, not random numbers**: a day-by-day process makes totals reconcile and lets relationships (promotion → demand → stock → cash) emerge.
* **Weighted-average cost** for inventory: simple, standard, and keeps COGS consistent.
* **Direct cash forecast, daily engine / weekly view**: shortfalls are judged on the daily path because a weekly closing balance can hide a mid-week dip.
* **Ridge regression** as the one ML model: interpretable coefficients and fast; compared against a seasonal-naive baseline with chronological validation.
