# Resume description and interview talking points

> **Be honest about how it was built.** This project was produced with an AI coding assistant. In an interview, say so plainly, and make sure you can explain every module yourself — the [five-week plan](architecture_and_plan.md#4-five-week-learning-plan-how-to-study-and-extend-the-project) is designed for exactly that. Do **not** claim business impact, real users, real customers or real model accuracy: none exist. Only claim what you have personally run and checked.

## Resume entry (edit to match what you actually did and understand)

**RetailIQ — Integrated Retail Business Intelligence & Planning Platform** *(personal project, Python)*

* Built a local Streamlit application on SQLite/SQLAlchemy that connects sales, finance, working capital, inventory, funding, marketing, customer behaviour and reviews for a **simulated** two-store retailer, with 13 interactive pages and CSV exports.
* Wrote a seeded, day-by-day data simulator (about 24 months, 28 tables) whose output reconciles by construction: transaction totals equal line items, stock movements equal balances, cash ledger equals cash rebuilt from source tables, and the balance sheet balances.
* Implemented finance logic with explicit accounting rules (COGS vs purchases, capex vs opex, interest vs principal, shared-cost allocation), a 13-week cash forecast, term-loan / revolver / owner-capital comparisons, and working-capital scenarios.
* Built inventory planning (safety stock, reorder points, case-pack rounding, budget-constrained prioritisation) and a connected marketing → stock → cash scenario.
* Developed marketing (ROAS vs contribution), customer (RFM, cohorts, lifetime value) and review (rule-based sentiment, themes) analytics, plus a seasonal-naive vs Ridge-regression sales forecast with chronological validation and leakage tests.
* Wrote a pytest suite (147 tests, including headless rendering of every page) and a rule-based "Action Center" in which every finding shows its trigger, numbers, period, action and limitations.

*One-line version:* "Built an end-to-end retail analytics and cash-planning app (Python, Streamlit, SQLite, scikit-learn) on a self-generated, reconciling synthetic dataset, with tested finance, inventory, funding and forecasting logic."

## Talking points by theme

### Why this project / the idea
* Owners ask connected questions ("can we afford this promotion?") but tools show one area at a time. RetailIQ makes the links explicit: marketing → demand → stock → purchasing → cash → funding.
* With no real dataset available I treated **data generation as part of the engineering**: a simulation with a fixed seed so the numbers are reproducible and consistent.

### Data and database
* **Grain** of every table is documented; aggregates are built at a common grain before joining, so attribution and reviews cannot double-count revenue.
* Idempotent initialisation: same seed = no change; different data = refuses unless `--reset`, which backs up first.
* Integrity: foreign keys enforced, orphan checks, and reconciliation checks (cash, stock, balance sheet).

### Finance (be ready to draw it)
* Gross profit ≠ net profit; purchases ≠ COGS; capex is depreciated; principal is not an expense; interest is a financing cost; loans and owner money are not revenue.
* Shared costs are allocated by share of net sales (or equally) and the app shows that store profits add up to the company total.
* Balance-sheet identity as an end-to-end test of the whole dataset.

### Working capital, inventory and funding
* Working capital = inventory + receivables − payables; cash conversion cycle = inventory days + receivable days − payable days.
* Safety stock = z·√(LT·σd² + d²·σLT²); reorder point = d·LT + safety stock; order quantities rounded up to case packs, capped by supplier limits, never negative.
* A purchase today becomes a cash payment weeks later (arrival + supplier terms); the 13-week forecast is built daily because a weekly closing balance can hide a mid-week dip.
* Funding options are compared under user-entered assumptions; the app deliberately does **not** pick a winner.

### Marketing, customers, reviews
* ROAS = revenue ÷ spend; contribution subtracts product cost, discounts and selling costs. Two simulated deep-discount campaigns show strong ROAS with weak profit.
* Attribution is single-touch by code redemption — it cannot prove causation; the app lets you haircut it with an explicit assumption.
* Sentiment baseline is a transparent dictionary method; its agreement with star ratings is inflated because the text is simulated — say this rather than quoting it as accuracy.

### Forecasting and quality
* Baseline (seasonal naive) vs Ridge regression, evaluated with MAE and WAPE on rolling-origin, chronological folds; WAPE instead of MAPE because many days have zero sales.
* Leakage prevention: shifted lags and rolling means, recursive multi-day forecasts, promotion flag from the plan, and tests that change future actuals and assert the forecast does not move.

### Testing
* Tests recompute key numbers from the raw tables (not through the code under test), include boundary cases (zero interest, zero sales, empty filters, short history), and render every page headlessly, including under a non-INR display currency.

## Honest limitations (say these before you are asked)
* Everything is simulated; patterns like the leaking bottle were built in on purpose.
* Forecasts, scenarios and funding comparisons are assumption-based; no tax, GST timing or accruals are modelled.
* Local single-user app: no authentication, no cloud, no live data.

## Likely questions
| Question | Short answer |
|---|---|
| How would you use real data? | Replace the generator with an importer that fills the same tables; run `check`; the analytics are unchanged. |
| Why Ridge and not a neural network or gradient boosting? | Interpretable, fast, easy to validate; the point was a sound evaluation method. Gradient boosting would be the next experiment. |
| How do you know the model is not leaking? | Features use only earlier days, forecasts are recursive, validation is chronological, and a test alters post-origin actuals and checks the output is identical. |
| What would you improve next? | PostgreSQL, authentication, a data-import layer, richer demand features (price, stock-outs), and an optional LLM narrative on top of the rules. |
