# Verification report

*What was actually run when RetailIQ was built, the real results, and — just as important — what was **not** verified.*
Everything below was executed on one Windows 11 computer with Python 3.13.3 (64-bit) — the computer and Python version you described — in temporary folders, not in `D:\RetailIQ`. Numbers describe the **simulated** default dataset (seed 42).

## 1. Environment

| Item | Value |
|---|---|
| OS | Windows 11 Home (10.0.26200) |
| Python | 3.13.3, 64-bit |
| Packages | streamlit 1.66.0 · pandas 3.0.6 · numpy 2.5.4 · plotly 7.1.0 · SQLAlchemy 2.1.5 · scikit-learn 1.9.1 · pytest 9.1.1 (pinned in `requirements.txt`; every installed package in `requirements-lock.txt`) |
| Isolation | Development used a throw-away virtual environment outside any other project. The final check below used a **brand-new virtual environment in a freshly unzipped copy**. No existing project or environment on the machine was touched (the Documind environments were not opened or modified). The throw-away environments and test data were deleted afterwards. |

## 2. Automated tests — actual results

```
platform win32 -- Python 3.13.3, pytest-9.1.1
collected 147 items
tests\test_actions_scenarios_ui.py ................................. 
tests\test_datagen_and_db.py ......................
tests\test_finance_capital.py ..............................
tests\test_forecasting.py ............
tests\test_inventory_funding_cash.py .................................
tests\test_marketing_customers_reviews.py .................
======================= 147 passed in 145.06s (0:02:25) =======================
```

The same 147 tests passed again in the fresh copy: `147 passed in 162.80s (0:02:42)`.

| Test file | Tests | What it proves |
|---|---|---|
| `test_datagen_and_db.py` | 22 | Identical data for the same seed; 2 stores / 20 products; transaction totals = line items; stock movements = balances; cash ledger = cash rebuilt from source tables; all identifiers valid; one campaign per sale; opening balances balance; `init-db` repeatable, never overwrites a different dataset silently, backs up before `--reset`; foreign keys enforced; every table documents its grain |
| `test_finance_capital.py` | 30 | Net sales, returns, COGS, gross profit, margin, operating profit and profit before tax recomputed from raw tables; purchases ≠ COGS; loans/owner money ≠ revenue; principal ≠ expense; interest separate; capex depreciated not expensed; shared-cost allocation sums to company total; cash reconciles; balance sheet balances on 8 dates; working-capital formulas; short-history and zero-sales handling; scenario direction and validation |
| `test_inventory_funding_cash.py` | 33 | Stock reconciliation; safety-stock/reorder-point formulas; case-pack rounding, caps, never negative; on-order stock reduces orders; budget allocation never exceeds budget and funds essentials first; loan schedules (interest/principal split, zero interest, grace months, boundaries, invalid input); effective cost; dilution; revolver limits; 13-week forecast identities; shortfall detection |
| `test_marketing_customers_reviews.py` | 17 | CTR/CPC/conversion/CPA/ROAS from raw data; attribution totals (no duplication); contribution ≠ ROAS; budget scenario; repeat rate, new/returning, RFM thresholds, cohorts, lifetime value requirements; sentiment, themes, search; complaint and rating-decline rules |
| `test_forecasting.py` | 12 | Seasonal naive; features use only earlier days; **changing every post-origin actual leaves the forecast identical** (leakage test); recursive forecasting; chronological folds never overlap; MAE/WAPE formulas; short-history/zero-sales safety; determinism |
| `test_actions_scenarios_ui.py` | 33 | Every Action Center rule returns a complete, traceable finding; known simulated issues are detected; thresholds control firing; a crashing rule cannot hide others; the connected scenario chain; formatting (Indian digit grouping, currency, periods); **every one of the 13 pages renders headlessly** on a small dataset; 5 pages render in USD with no INR leaking in; the app offers setup when no database exists |

## 3. Command-line checks (actual output)

`python -m retailiq.cli check` on a freshly created default database:

```
[PASS] referential integrity - (7 checks): 0 orphan rows each
[PASS] transaction totals equal the sum of their lines: largest difference 0.0000
[PASS] stock movements reconcile with stock balances: largest quantity difference 0, value difference 0.00
[PASS] cash ledger equals cash rebuilt from source tables (2025-09-30): 4,513,009.95 vs 4,513,009.95
[PASS] balance sheet balances (2025-09-30): assets - liabilities - equity = 0.00
[PASS] cash ledger equals cash rebuilt from source tables (2026-09-30): 4,806,012.93 vs 4,806,012.93
[PASS] balance sheet balances (2026-09-30): assets - liabilities - equity = -0.00
All checks passed.
```

`init-db` took about 15 seconds. Running it a second time printed *"Nothing was changed."* Running it with a different seed was refused. The fresh copy produced the identical cash figures above, confirming reproducibility across environments.

## 4. Fresh-install rehearsal of the README

In a new folder, from the zip contents only: created `.venv` → `pip install -r requirements.txt` (220 s; `pip check`: no broken requirements) → `init-db` → `check` → `export` (28 CSV files) → `pytest` (147 passed) → `streamlit run` (health endpoint "ok", home page HTTP 200) → `scripts\setup.ps1` (exit 0, safely reuses the existing environment and database) → `scripts\run.ps1` (app healthy on 127.0.0.1:8501). The app listens only on 127.0.0.1 (a LAN address could not connect).

## 5. Headline numbers from the default simulated dataset

| | |
|---|---|
| Period | 1 Oct 2024 – 30 Sep 2026 (24 months), 2 stores, 20 products, 6 suppliers, 1,240 customers |
| Rows | 45,441 transactions · 72,394 lines · 1,546 returns · 488 purchase orders · 74,477 stock movements · 47,902 cash-ledger rows · 1,936 reviews |
| 24-month net sales / gross margin | ₹4.93 crore / 40.0% |
| 24-month operating profit | ₹22.9 lakh (4.6% margin); profit before tax ₹19.7 lakh |
| At 30 Sep 2026 | cash ₹48.1 lakh · inventory ₹17.7 lakh · receivables ₹2.8 lakh · payables ₹17.2 lakh · loans ₹15.0 lakh |
| 13-week base forecast | lowest cash ₹25.1 lakh on 1 Dec 2026 (no shortfall against a ₹7.5 lakh reserve); the optional example ₹30 lakh outflow on the Funding tab creates a gap of about ₹12.4 lakh |
| Forecast validation (6 rolling origins) | 7-day: model MAE 2.09 vs baseline 2.86 units/day, WAPE 54.5% vs 74.5%; 30-day: MAE 2.07 vs 2.91, total-demand WAPE 18.6% vs 30.7% |
| Customers | repeat-purchase rate 73.0%; annual retention 40.5%; simple lifetime value ≈ ₹4,700 gross profit |

These figures come from the *simulated* data and the *simulated* demand process (which the forecast model partly mirrors). They show the methods working; they are **not** evidence of real-world accuracy or business impact.

## 6. Interface verification

* **Headless rendering** of all 13 pages (tests above), also with the reference date moved to four awkward values (first day of data, early in the history, mid-month) — 48 page renders, no exceptions or unexpected errors — and in USD.
* **Live app**: launched with Streamlit and opened in a browser pane; the Executive overview, Action center, Cash forecast & funding and Marketing pages were read and viewed, and five screenshots were saved in `docs/screenshots/`.
* Defects found and fixed during this verification (examples): a monthly chart distorted by a 2-day partial month; an inappropriate "attribution check failed" caused by comparing a period-filtered table with all-time totals; a Streamlit "magic" crash from a ternary statement; integer column names breaking the table helper; days metrics being shown for a 1-day window (now n/a); a server that listened on all network interfaces (now localhost only).

## 7. What was NOT verified (be aware)

* **Not every page was viewed visually** in a real browser. The browser pane drew only intermittently, so most pages were verified through headless rendering and text, not by eye. Layout polish (column widths, label overlap in crowded scatter plots) may need small tweaks. Dark mode and phone-sized screens were not checked visually.
* **Not run in your final location.** Everything was run on the same Windows 11 computer and Python version you described, but inside temporary folders on `C:` — not in `D:\RetailIQ`. The scripts were run with `-ExecutionPolicy Bypass`, so the behaviour under your own PowerShell execution policy was not tested (the README shows the policy-proof `.\.venv\Scripts\python.exe` form). Timings (15 s data generation, ~2–3 minutes for the full test suite) will vary.
* **No performance testing** beyond observing that pages respond within a few seconds after the first (cold) load, which takes about 10 seconds because caches are empty.
* The **sentiment baseline**, **forecast scores**, **funding comparisons** and **scenarios** were checked for correct arithmetic and behaviour, **not** for real-world validity — there is no real data.
* Multi-user use, authentication, cloud hosting and PostgreSQL were not built or tested.
