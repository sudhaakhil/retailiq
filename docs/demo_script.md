# Demonstration script (about 10 minutes)

**Before you start:** `.\.venv\Scripts\python.exe -m retailiq.cli init-db` (once), then `... -m streamlit run streamlit_app.py`. Keep the reference date at its default (latest date). Say up front: *"All data is simulated; I built the generator so the numbers reconcile."*

The numbers quoted below are what the default seed (42) produced when this project was built. If you regenerate with another seed, the story changes — read the screen, not this script.

| # | Page | What to do and say | Takeaway |
|---|---|---|---|
| 1 | **Executive overview** (1 min) | Point to the two headings: *measured over a period* vs *balances at a date*. Last 3 months: net sales ≈ ₹67 lakh, up about 11% on the previous period, but **gross margin down 2.7 points** and operating margin only 2.3%. | Growth ≠ profit. |
| 2 | **Action center** (1.5 min) | Open the top findings. Click *"Sales are growing but operating profit is declining"* — show the rule, the numbers (net sales +12.8% vs year ago while operating profit fell) and the limitation text. | Every finding is traceable: rule → numbers → period → action → limits. No AI API involved. |
| 3 | **Finance** (1.5 min) | *Profit & loss*: read the statement top to bottom; say "gross profit is not net profit". Switch *Cash flow*: opening + in − out = closing, with the independent cross-check. *Balance sheet*: "assets = liabilities + equity, difference 0.00". | The books reconcile; financing is not revenue; principal is not an expense. |
| 4 | **Working capital** (1 min) | Cash conversion cycle ≈ 5 days (inventory 38 + receivable 4 − payable 37). In the **Scenario** tab set sales growth +20% and inventory 60 days; show the sensitivity bars. | Inventory days dominate the cash need; faster-growing sales barely move it. |
| 5 | **Inventory** (1.5 min) | *Stock health*: one line out of stock, one slow-moving (speaker). *Reorder plan*: pick a line, show lead time, safety stock, reorder point, case-pack rounding. Turn on a small **budget** and show essentials funded first. | Replenishment is tied to cash; the budget rule is transparent. |
| 6 | **Cash forecast & funding** (2 min) | 13-week chart and weekly table. In *Funding comparison* the example ₹30 lakh outflow creates a gap; show term loan vs owner capital vs revolver vs operational changes, the repayment schedule (interest/principal separated) and the effective annual cost. Say: "this compares consequences under my assumptions; it does not pick a winner." | Funding is a cash-timing and cost trade-off. |
| 7 | **Marketing** (1 min) | Set the period to **All data**. In the scatter *ROAS vs contribution*, the **Earbuds Mega Deal** has ROAS ≈ 13× but a slightly negative contribution because of its 35% discount (the **Monsoon Electronics Flash Sale** shows ROAS ≈ 10× with the same problem). | ROAS is not profit. Attribution is not causation (open *Attribution method* and the check). |
| 8 | **Customers / Reviews** (1 min) | Customers: repeat rate ≈ 73%, cohort heatmap, customers becoming inactive. Reviews: Steel Water Bottle falling to ~2.2 stars; search "leaking"; mention the sentiment baseline's agreement is inflated because the text is simulated. | Honest limits of the methods. |
| 9 | **Sales forecasting** (0.5 min) | Pick a product; show model vs baseline and the validation table (model beats baseline on MAE). Mention shifted lags, recursive forecasts, chronological folds. | No leakage; scores are for simulated data only. |
| 10 | **Connected scenario** (1 min) | Choose the planned Diwali 2026 campaign, +50% spend. Walk the six steps: spend → revenue → units → stock to buy → purchasing cost → lowest cash. | The headline requirement: one decision ripples through every area. |

**Closing line:** *"Everything is reproducible: one command builds the data, one command checks it, one command runs the tests, and every number traces back to rows in the database."*

### Questions to expect — and answers
* *"Is the data real?"* No — simulated with a fixed seed; I documented the assumptions and the patterns I built in on purpose.
* *"How do you know the numbers are right?"* Reconciliation checks (cash, stock, balance sheet) and a test suite that recomputes values directly from raw tables.
* *"What would you change for real data?"* Replace the generator with an importer that fills the same tables, then re-run the checks.
