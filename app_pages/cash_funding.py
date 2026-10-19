import math

import numpy as np
import pandas as pd
import streamlit as st

from retailiq.analytics import cashflow as CF
from retailiq.analytics import funding as FU
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import money, pct

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("When might cash run short, how big could the gap be, and how would different ways of funding it change the picture? "
         "Everything here is an **assumption-based projection**, not a prediction and not a bank offer.")
C.note("info", f"Forecast starts the day after the reference date ({(ds.ref_date + pd.Timedelta(days=1)):%d %b %Y}) and runs 13 weeks. Rates, fees, dates and amounts for funding are your own inputs.")

# ------------------------------------------------------------------ session defaults
DEFAULTS = {"cf_sales_adj": 0.0, "cf_coll_delay": 0, "cf_sup_delay": 0, "cf_cover": 40, "cf_reserve": 750000.0, "cf_mkt": 0.0, "cf_inv_cut": 0.0}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
if "cf_planned" not in st.session_state or st.session_state.get("cf_planned_key") != state.db_signature():
    pl = ds.t["planned_cash_items"][["planned_date", "category", "description", "amount", "activity"]].copy()
    st.session_state["cf_planned"] = pl
    st.session_state["cf_planned_key"] = state.db_signature()


def preset(name: str) -> None:
    if name == "base":
        st.session_state.update({"cf_sales_adj": 0.0, "cf_coll_delay": 0, "cf_sup_delay": 0, "cf_mkt": 0.0, "cf_inv_cut": 0.0})
    elif name == "downside":
        st.session_state.update({"cf_sales_adj": -20.0, "cf_coll_delay": 15, "cf_sup_delay": 0, "cf_mkt": 0.0, "cf_inv_cut": 0.0})
    elif name == "severe":
        st.session_state.update({"cf_sales_adj": -35.0, "cf_coll_delay": 30, "cf_sup_delay": 0, "cf_mkt": 0.0, "cf_inv_cut": 0.0})


def reset_planned() -> None:
    st.session_state.pop("cf_planned_editor", None)  # drops the editor's saved edits; the stable source below is unchanged


def planned_records(df: pd.DataFrame) -> tuple:
    d = df.dropna(subset=["planned_date", "amount"]).copy()
    d["planned_date"] = pd.to_datetime(d["planned_date"]).dt.strftime("%Y-%m-%d")
    d["category"] = d["category"].fillna("Other")
    d["description"] = d["description"].fillna("")
    d["activity"] = d["activity"].fillna("investing")
    return tuple(tuple(r) for r in d[["planned_date", "category", "description", "amount", "activity"]].itertuples(index=False, name=None))


tab_fc, tab_fund = st.tabs(["13-week cash forecast", "Funding comparison"])

# ================================================================== forecast
with tab_fc:
    with st.container(horizontal=True):
        st.button("Base case", on_click=preset, args=("base",), icon=":material/restart_alt:", help="Seasonal baseline with no stress.")
        st.button("Downside: sales −20%, credit customers 15 days late", on_click=preset, args=("downside",), icon=":material/trending_down:")
        st.button("Severe: sales −35%, credit customers 30 days late", on_click=preset, args=("severe",), icon=":material/emergency:")
    with st.expander("Forecast assumptions", icon=":material/tune:", expanded=True):
        a1, a2, a3, a4 = st.columns(4)
        sales_adj = a1.number_input("Sales vs seasonal baseline (%)", -90.0, 200.0, step=5.0, key="cf_sales_adj", help="Baseline = same weekday last year × recent growth. 0 = the baseline.")
        coll_delay = a2.number_input("Credit customers pay later by (days)", -20, 90, step=1, key="cf_coll_delay", help="Applies to business customers on credit terms only. Card/UPI settle via the processor.")
        sup_delay = a3.number_input("We pay suppliers later by (days)", -20, 60, step=1, key="cf_sup_delay", help="Positive = stretch payables (check this is agreed with suppliers).")
        cover = a4.number_input("Stock cover to order up to (days)", 5, 120, step=5, key="cf_cover", help="Drives how much new stock the forecast buys.")
        b1, b2, b3 = st.columns(3)
        reserve = b1.number_input(f"Minimum cash reserve ({cur.code})", min_value=0.0, value=float(round(st.session_state["cf_reserve"] * cur.rate)), step=50000.0 * cur.rate, key=f"cf_reserve_disp_{cur.code}") / cur.rate
        st.session_state["cf_reserve"] = reserve
        mkt = b2.number_input("Marketing budgets vs plan (%)", -100.0, 300.0, step=10.0, key="cf_mkt")
        inv_cut = b3.number_input("Buy less stock by (%)", 0.0, 90.0, step=5.0, key="cf_inv_cut", help="Operational lever: lower purchase quantities (raises stock-out risk).")
    st.markdown("**Known future cash items** (capex, drawings, tax, ...) - edit, add or delete rows. Amounts in INR; negative = cash out.")
    edited = st.data_editor(
        st.session_state["cf_planned"], num_rows="dynamic", hide_index=True, key="cf_planned_editor", alt="Planned future cash items",
        column_config={"planned_date": st.column_config.DateColumn("Date", min_value=(ds.ref_date + pd.Timedelta(days=1)).date(), max_value=(ds.ref_date + pd.Timedelta(days=91)).date(), required=True),
                       "category": st.column_config.TextColumn("Category"), "description": st.column_config.TextColumn("Description", width="large"),
                       "amount": st.column_config.NumberColumn("Amount (INR)", format="%,.0f", required=True),
                       "activity": st.column_config.SelectboxColumn("Type", options=["investing", "financing", "operating"], required=True)})
    st.caption("Edits live in this browser session only and do not change the database. "
               "Items dated outside the 13-week window are ignored.")
    st.button("Reset to the database's planned items", on_click=reset_planned, icon=":material/undo:")
    res = cached.cash_forecast(ds, key, sales_adj / 100, int(coll_delay), int(sup_delay), int(cover), float(reserve), mkt / 100, inv_cut / 100, planned_records(edited))
    sm, daily, weekly = res["summary"], res["daily"], res["weekly"]

    if sm["breach"]:
        st.error(f"**Projected cash falls below the reserve** - first on {sm['first_breach_date']:%d %b %Y} (week {sm['first_breach_week']}); lowest point {money(sm['lowest_cash'], cur, compact=True)} on "
                 f"{sm['lowest_cash_date']:%d %b %Y}; largest shortfall against the reserve {money(sm['max_shortfall'], cur, compact=True)}; {sm['days_below_reserve']} days below the reserve.",
                 icon=":material/warning:")
    else:
        st.success(f"Cash stays above the {money(reserve, cur, compact=True)} reserve for all 13 weeks. Lowest point {money(sm['lowest_cash'], cur, compact=True)} on {sm['lowest_cash_date']:%d %b %Y} "
                   f"(headroom {money(sm['lowest_cash'] - reserve, cur, compact=True)}).", icon=":material/check_circle:")
    with st.container(horizontal=True):
        C.kpi("Opening cash", M(sm["opening_cash"]), help=f"Balance at {ds.ref_date:%d %b %Y}")
        C.kpi("Lowest projected cash", M(sm["lowest_cash"]), help=f"On {sm['lowest_cash_date']:%d %b %Y}")
        C.kpi("Closing cash (week 13)", M(sm["closing_cash_end"]))
        C.kpi("Reserve", M(reserve))
        C.kpi("Net cash flow, 13 weeks", M(daily["net_cash_flow"].sum()))
    d = C.fx_df(daily, ["closing_cash", "net_cash_flow"])
    fig = CH.cash_path(d, {"Projected closing cash": "closing_cash"}, reserve * cur.rate, "Projected daily closing cash", 380)
    CH.show(fig, "Projected daily closing cash against the minimum reserve", key="cf_cash_path")

    wt = CF.weekly_table_for_display(weekly).reset_index(names="line")
    wcols = [c for c in wt.columns if c != "line"]
    st.markdown("**Weekly cash-flow statement**")
    C.table(wt, money_cols=wcols, name="cash_forecast_weekly", alt="Weekly cash flow forecast", columns=["line"] + wcols)
    C.note("info", "Each week is seven days starting on the first forecast day. 'Lowest daily cash in week' can be below the weekly closing figure, so shortfalls are judged on the daily path.")
    with st.expander("How each line is projected (and what could go wrong)", icon=":material/help:"):
        st.markdown(
            "- **Sales**: same weekday one year earlier × each store's recent year-on-year growth × (1 + your adjustment). Festival dates that move between years (e.g. Diwali) are not shifted.\n"
            "- **Collections**: card/UPI/cash settle in 0-2 days; business-credit sales follow the last 12 months' collection pattern; invoices already outstanding are collected on their due date plus typical lateness.\n"
            "- **Supplier payments**: unpaid invoices on the due date; open purchase orders arrive at the expected date plus typical lateness and are paid after the supplier's terms; "
            "**new purchase orders** come from a day-by-day replenishment simulation using the reorder rules on the Inventory page.\n"
            "- **Operating expenses**: fixed items repeat last month's amounts on the same day; variable costs follow their recent ratio to sales; marketing follows planned campaign budgets.\n"
            "- **Loans**: contractual instalments, with interest and principal shown separately. **Capex / drawings**: the planned-items table above.\n"
            "- **Not modelled**: income tax, GST timing, price changes, one-off events, seasonality shifts, stock-outs reducing sales.")
        g = res["growth"]
        st.caption("Store growth factors used: " + ", ".join(f"{ds.store_names[s]}: {v:.2f}×" for s, v in g.items()) + f" · refund rate {pct(res['refund_rate'])} of sales")
        for n in res["notes"]:
            C.note("warn", n)
    with st.expander("Purchase orders the simulation expects to place", icon=":material/inventory:"):
        no = res["new_orders"]
        if no.empty:
            st.write("No new purchase orders are triggered in the 13 weeks.")
        else:
            C.table(no, money_cols=["unit_cost", "cost"], count_cols=["quantity"], name="forecast_purchase_orders", alt="Forecast purchase orders", money_decimals=0)
    st.download_button("Download daily forecast (CSV)", daily.to_csv(index=False).encode("utf-8-sig"), "retailiq_cash_forecast_daily.csv", "text/csv", icon=":material/download:")

# ================================================================== funding comparison
with tab_fund:
    st.markdown("Compare ways to cover a gap. Each option is applied to the **same** forecast so the cash paths are comparable. "
                "They show consequences under your assumptions - **they do not say which option is best**, because risk, security demanded by lenders, control and tax are not modelled.")
    st.markdown("**Optional what-if**")
    w1, w2, w3 = st.columns(3)
    use_whatif = w1.toggle("Add an extra one-off cash outflow to this comparison", value=True, key="fund_whatif",
                           help="An example expansion or large purchase that is not in the database, so there is something to fund. Switch off to use the forecast exactly as on the previous tab.")
    whatif_amt = w2.number_input(f"Amount ({cur.code})", min_value=0.0, value=float(round(3_000_000 * cur.rate, -3)), step=100000.0 * cur.rate, key=f"fund_whatif_amt_{cur.code}", disabled=not use_whatif) / cur.rate
    whatif_date = w3.date_input("Date", value=(ds.ref_date + pd.Timedelta(days=45)).date(), min_value=(ds.ref_date + pd.Timedelta(days=1)).date(),
                                max_value=(ds.ref_date + pd.Timedelta(days=91)).date(), key="fund_whatif_date", disabled=not use_whatif)
    recs = list(planned_records(edited))
    if use_whatif and whatif_amt > 0:
        recs.append((pd.Timestamp(whatif_date).strftime("%Y-%m-%d"), "Capital expenditure", "What-if: expansion or large purchase", -whatif_amt, "investing"))
    base = cached.cash_forecast(ds, key, sales_adj / 100, int(coll_delay), int(sup_delay), int(cover), float(reserve), mkt / 100, inv_cut / 100, tuple(recs))
    bd = base["daily"]
    bsm = base["summary"]
    gap = FU.cash_gap_to_fund(bd, reserve)
    with st.container(horizontal=True):
        C.kpi("Lowest cash without funding", M(bsm["lowest_cash"]), help=f"On {bsm['lowest_cash_date']:%d %b %Y}")
        C.kpi("Reserve", M(reserve))
        C.kpi("Gap to the reserve", M(gap), delta="shortfall" if gap > 0 else None, delta_color="inverse", help="Smallest one-off injection at the start that keeps cash at or above the reserve.")
        C.kpi("First day below reserve", f"{bsm['first_breach_date']:%d %b %Y}" if bsm["breach"] else "None")
    first_day = bd["date"].iloc[0]
    breach_day = bsm["first_breach_date"] if bsm["breach"] else first_day + pd.Timedelta(days=14)
    default_date = max((breach_day - pd.Timedelta(days=7)).date(), first_day.date())
    suggested = float(max(math.ceil(gap * 1.1 / 100000) * 100000, 500000))

    o1, o2, o3 = st.columns(3)
    with o1:
        with st.container(border=True):
            st.markdown("**Owner capital injection**")
            eq_on = st.toggle("Include", value=True, key="eq_on")
            eq_amt = st.number_input(f"Amount ({cur.code})", min_value=0.0, value=float(round(suggested * cur.rate)), step=100000.0 * cur.rate, key=f"eq_amt_{cur.code}") / cur.rate
            eq_date = st.date_input("Date received", value=default_date, min_value=first_day.date(), max_value=bd["date"].iloc[-1].date(), key="eq_date")
            pre_money = st.number_input(f"Pre-money valuation, optional ({cur.code})", min_value=0.0, value=0.0, step=500000.0 * cur.rate, key=f"eq_val_{cur.code}",
                                        help="Only for the simple dilution estimate. Leave at 0 to skip.") / cur.rate
    with o2:
        with st.container(border=True):
            st.markdown("**Term loan**")
            tl_on = st.toggle("Include", value=True, key="tl_on")
            tl_amt = st.number_input(f"Principal ({cur.code})", min_value=0.0, value=float(round(suggested * cur.rate)), step=100000.0 * cur.rate, key=f"tl_amt_{cur.code}") / cur.rate
            tl_rate = st.number_input("Interest rate (% per year)", min_value=0.0, max_value=60.0, value=11.5, step=0.25, key="tl_rate", help="Your assumption - not a market quote. 0 is allowed.") / 100
            tl_term = st.number_input("Term (months)", min_value=1, max_value=120, value=36, step=1, key="tl_term")
            tl_grace = st.number_input("Interest-only months", min_value=0, max_value=int(tl_term) - 1, value=0, step=1, key="tl_grace")
            tl_fee = st.number_input("Upfront fee (% of principal)", min_value=0.0, max_value=10.0, value=1.0, step=0.25, key="tl_fee") / 100
            tl_date = st.date_input("Drawdown date", value=default_date, min_value=first_day.date(), max_value=bd["date"].iloc[-1].date(), key="tl_date")
    with o3:
        with st.container(border=True):
            st.markdown("**Revolving credit facility**")
            rc_on = st.toggle("Include", value=True, key="rc_on")
            rc_limit = st.number_input(f"Limit ({cur.code})", min_value=0.0, value=float(round(suggested * 1.5 * cur.rate)), step=100000.0 * cur.rate, key=f"rc_limit_{cur.code}") / cur.rate
            rc_rate = st.number_input("Interest on drawn balance (% per year)", min_value=0.0, max_value=60.0, value=13.0, step=0.25, key="rc_rate") / 100
            rc_fee = st.number_input("Upfront fee (% of limit)", min_value=0.0, max_value=10.0, value=0.5, step=0.25, key="rc_fee") / 100
            rc_undrawn = st.number_input("Commitment fee on undrawn (% per year)", min_value=0.0, max_value=10.0, value=1.0, step=0.25, key="rc_undrawn") / 100
            rc_date = st.date_input("Facility starts", value=first_day.date(), min_value=first_day.date(), max_value=bd["date"].iloc[-1].date(), key="rc_date")
    with st.container(border=True):
        st.markdown("**Operational changes (no new money)**")
        p1, p2, p3 = st.columns(3)
        op_inv = p1.number_input("Buy less stock by (%)", 0.0, 90.0, 20.0, 5.0, key="op_inv", help="Lower purchase quantities during the 13 weeks.")
        op_sup = p2.number_input("Pay suppliers later by (days)", 0, 60, 10, 1, key="op_sup", help="Requires supplier agreement.")
        op_col = p3.number_input("Collect credit invoices sooner by (days)", 0, 30, 10, 1, key="op_col", help="Applies to business customers on credit terms.")
        op_on = st.toggle("Include operational changes", value=True, key="op_on")

    results, errors = [FU.baseline_option(bd)], []
    try:
        if eq_on and eq_amt > 0:
            results.append(FU.owner_capital_option(bd, eq_amt, eq_date, pre_money or None))
        if tl_on and tl_amt > 0:
            results.append(FU.term_loan_option(bd, tl_amt, tl_rate, int(tl_term), tl_date, tl_fee, int(tl_grace)))
        if rc_on and rc_limit > 0:
            results.append(FU.revolver_option(bd, rc_limit, rc_rate, reserve, rc_date, rc_fee, rc_undrawn))
        if op_on:
            ops = cached.cash_forecast(ds, key, sales_adj / 100, int(coll_delay) - int(op_col), int(sup_delay) + int(op_sup), int(cover), float(reserve), mkt / 100,
                                       min(inv_cut + op_inv, 95.0) / 100, tuple(recs))
            r = FU.baseline_option(ops["daily"], "Operational changes only")
            results.append(r)
    except ValueError as exc:
        errors.append(str(exc))
    for e in errors:
        st.error(e)
    cmp_df = FU.compare_options(results, reserve)
    st.subheader("Comparison", anchor=False)
    C.table(cmp_df, columns=["option", "lowest_cash", "lowest_cash_date", "days_below_reserve", "remaining_shortfall_vs_reserve", "closing_cash_end", "interest_in_horizon", "fees_in_horizon",
                             "principal_repaid_in_horizon", "debt_outstanding_end", "interest_over_life", "effective_annual_cost"],
            money_cols=["lowest_cash", "remaining_shortfall_vs_reserve", "closing_cash_end", "interest_in_horizon", "fees_in_horizon", "principal_repaid_in_horizon", "debt_outstanding_end", "interest_over_life"],
            pct_cols=["effective_annual_cost"], count_cols=["days_below_reserve"], labels={"remaining_shortfall_vs_reserve": "Shortfall left vs reserve", "interest_in_horizon": "Interest (13 wks)",
                                                                                         "fees_in_horizon": "Fees (13 wks)", "principal_repaid_in_horizon": "Principal repaid (13 wks)",
                                                                                         "debt_outstanding_end": "Debt at week 13", "interest_over_life": "Interest over loan life",
                                                                                         "effective_annual_cost": "Effective annual cost"}, name="funding_comparison", alt="Funding option comparison")
    C.note("info", "'Operational changes only' re-runs the forecast with lower purchases, later supplier payments and faster credit collections, and no borrowing. "
                   "Effective annual cost is the annualised internal rate of return of the borrowing including fees; it is blank where it is not defined.")
    paths = pd.DataFrame({"date": bd["date"]})
    for r in results:
        paths[r.name] = r.daily["closing_cash"].values
    pcols = {r.name: r.name for r in results}
    fig = CH.cash_path(C.fx_df(paths, list(pcols)), pcols, reserve * cur.rate, "Closing cash under each option", 420)
    CH.show(fig, "Closing cash under each funding option compared with the minimum reserve", key="fund_paths")

    for r in results:
        if r.notes:
            for n in r.notes:
                C.note("warn", f"{r.name}: {n}")
    detail = st.segmented_control("Show details for", [r.name for r in results if r.name not in ("No funding action", "Operational changes only")], default=None, key="fund_detail")
    chosen = next((r for r in results if r.name == detail), None)
    if chosen is not None and chosen.schedule is not None:
        s = chosen.schedule
        st.markdown(f"**Repayment schedule - {chosen.name}** (principal and interest are separate; the loan itself is a financing inflow, not revenue)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total interest over the loan", money(s["interest"].sum(), cur, compact=True), border=True)
        c2.metric("Upfront fee", money(s.attrs.get("upfront_fee", chosen.life_fees or 0), cur, compact=True), border=True)
        c3.metric("Effective annual cost", pct(chosen.effective_rate) if chosen.effective_rate is not None else "n/a", border=True)
        fig = CH.bars(C.fx_df(s, ["interest", "principal"]).assign(date=s["payment_date"]), "date", {"Interest": "interest", "Principal": "principal"}, "Instalments split into interest and principal", stacked=True)
        CH.show(fig, "Loan instalments split into interest and principal", key="fund_sched_chart")
        C.table(s, columns=["instalment_no", "payment_date", "opening_balance", "payment", "interest", "principal", "closing_balance"], money_cols=["opening_balance", "payment", "interest", "principal", "closing_balance"],
                count_cols=["instalment_no"], money_decimals=0, name="loan_schedule", alt="Loan repayment schedule")
    elif chosen is not None and chosen.name == "Revolving credit facility":
        dd = C.fx_df(chosen.daily, ["debt_balance", "closing_cash"])
        fig = CH.lines(dd, "date", {"Drawn balance": "debt_balance"}, "Revolving facility - drawn balance")
        CH.show(fig, "Drawn balance of the revolving credit facility over time", key="fund_rc_chart")
        st.caption(f"Peak drawn {money(chosen.extra['peak_drawn'], cur, compact=True)} of a {money(chosen.extra['limit'], cur, compact=True)} limit. Rule: draw only when cash would close below the reserve; repay "
                   "whenever cash is above it. Interest and commitment fees are paid at each month end.")
    elif chosen is not None and chosen.extra:
        e = chosen.extra
        st.markdown("**Simple ownership dilution (illustrative)**")
        with st.container(horizontal=True):
            C.kpi("Post-money valuation", M(e["post_money_valuation"]))
            C.kpi("New investor ownership", pct(e["new_investor_pct"]))
            C.kpi("Existing owner after", pct(e["existing_owner_pct_after"]))
        st.caption(e["note"])
    elif chosen is not None and chosen.name == "Owner capital injection":
        st.caption("No valuation entered, so no dilution estimate. Owner capital carries no interest or repayment, but has an opportunity cost and may dilute ownership if investors join.")
    C.definition("the funding comparison", "- **Owner capital**: cash in on the chosen date; no interest or repayment.\n- **Term loan**: principal received (less the upfront fee) on the drawdown date; equal monthly instalments "
                                           "(interest-only during any grace months). Interest each month = opening balance × annual rate ÷ 12; zero-interest loans simply repay principal.\n"
                                           "- **Revolving facility**: simulated daily - draws just enough to hold the reserve, repays from surplus cash; interest on drawn balance and a commitment fee on the unused part.\n"
                                           "- **Operational changes**: less stock bought, suppliers paid later, credit customers collected sooner - they release cash but may cost availability or goodwill.\n"
                                           "- Costs shown cover the forecast window and, for the term loan, the whole life. Tax effects are ignored.")
