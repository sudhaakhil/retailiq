import pandas as pd
import streamlit as st

from retailiq.analytics import finance as F
from retailiq.analytics.accounting import safe_div
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui import state
from retailiq.ui.formatting import delta_text, money, pct, points_delta

ds = state.get_dataset()
cur = state.currency()
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("Are we actually profitable, and where does the cash go? Accounting rules are applied consistently: "
         "**gross profit is not net profit**, loans and owner money are not revenue, and loan principal is not an expense.")

tabs = st.tabs(["Profit & loss", "Cash flow", "Budget vs actual", "Product & store profit", "Break-even", "Balance sheet"])

# ================================================================= Profit & loss
with tabs[0]:
    p = C.period_picker(ds, "fin_pl", default="Last 12 months")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        stores = st.multiselect("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], placeholder="Whole company", key="fin_store")
        alloc = st.segmented_control("Share head-office costs by", ["net_sales", "equal"], default="net_sales", required=True, key="fin_alloc",
                                     format_func=lambda x: {"net_sales": "Share of net sales", "equal": "Equal split"}[x],
                                     help="Only used when a single store (or not all stores) is selected. Whole-company figures include 100% of shared costs.")
    sel = stores or None
    pl = F.pnl(ds, p["start"], p["end"], sel, alloc)
    pl0 = F.pnl(ds, *p["cmp"], sel, alloc) if p["cmp"] else None
    if sel and len(sel) < len(ds.store_ids):
        C.note("info", f"Shared (head-office) costs, depreciation and interest are allocated to the selected store(s) at {pl['hq_allocation_share']:.1%} "
                       f"({'share of net sales' if alloc == 'net_sales' else 'equal split'}). Marketing campaigns without a store are shared costs.")

    with st.container(horizontal=True):
        g = lambda k: pl0[k] if pl0 else None  # noqa: E731
        C.kpi("Net sales", M(pl["net_sales"]), delta_text(pl["net_sales"], g("net_sales")))
        C.kpi("Gross profit", M(pl["gross_profit"]), delta_text(pl["gross_profit"], g("gross_profit")), help="Before operating costs - not net profit.")
        C.kpi("Operating profit", M(pl["operating_profit"]), delta_text(pl["operating_profit"], g("operating_profit")))
        C.kpi("Profit before tax", M(pl["profit_before_tax"]), delta_text(pl["profit_before_tax"], g("profit_before_tax")),
              help="After loan interest. No income tax is modelled.")
        C.kpi("Operating margin", pct(pl["operating_margin"]), points_delta(pl["operating_margin"], g("operating_margin")))

    st.subheader("Income statement", anchor=False)
    stmt = F.pnl_statement(pl).rename(columns={"amount": "current"})
    if pl0:
        stmt["comparison"] = F.pnl_statement(pl0)["amount"].values
        stmt["change"] = stmt["current"] - stmt["comparison"]
    C.table(stmt, money_cols=[c for c in ["current", "comparison", "change"] if c in stmt], name="income_statement", alt="Income statement", labels={"line": "Line", "note": "How to read it"},
            columns=[c for c in ["line", "current", "comparison", "change", "note"] if c in stmt])
    C.note("info", f"Current period: {p['label']}." + (f" Comparison: {p['cmp_label']}." if p["cmp"] else ""))

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            labels = ["Net sales", "Cost of goods sold", "Operating expenses", "Depreciation", "Operating profit", "Interest", "Profit before tax"]
            vals = [pl["net_sales"], -pl["cogs"], -pl["operating_expenses"], -pl["depreciation"], pl["operating_profit"], -pl["interest_expense"], pl["profit_before_tax"]]
            vals = [v * cur.rate for v in vals]
            fig = CH.waterfall(labels, vals, ["absolute", "relative", "relative", "relative", "total", "relative", "total"], "From net sales to profit before tax")
            CH.show(fig, "Waterfall from net sales to profit before tax", key="fin_waterfall")
    with c2:
        with st.container(border=True):
            ot = pl["opex_table"]
            C.table(ot, columns=["category", "behaviour", "direct", "allocated_shared", "total"], money_cols=["direct", "allocated_shared", "total"],
                    labels={"allocated_shared": "Allocated shared"}, name="opex_by_category", alt="Operating expenses by category")
            C.note("info", "Capital expenditure is not in operating expenses: it is capitalised and charged gradually as depreciation.")

    st.subheader("Monthly trend", anchor=False)
    mp_all = F.monthly_pnl(ds, p["start"], p["end"], sel)
    mp = mp_all[mp_all["full_month"]]
    if len(mp) < len(mp_all):
        C.note("info", "Months only partly inside the selected period are left out of the chart and table: fixed costs are booked on their payment dates, so part-months give misleading margins.")
    if mp.empty:
        C.empty_state("No complete calendar month falls inside the selected period.")
    else:
        fig = CH.bar_line(C.fx_df(mp, ["net_sales", "gross_profit", "operating_profit"]), "month",
                          {"Net sales": "net_sales", "Gross profit": "gross_profit", "Operating profit": "operating_profit"}, {"Gross margin": "gross_margin"},
                          "Monthly sales, profit and gross margin", line_is_pct=True)
        CH.show(fig, "Monthly net sales, gross profit and operating profit with gross margin", key="fin_trend")
        C.table(mp, columns=["month", "net_sales", "cogs", "gross_profit", "gross_margin", "operating_expenses", "depreciation", "operating_profit", "interest_expense", "profit_before_tax"],
                money_cols=["net_sales", "cogs", "gross_profit", "operating_expenses", "depreciation", "operating_profit", "interest_expense", "profit_before_tax"],
                pct_cols=["gross_margin"], name="monthly_pnl", alt="Monthly profit and loss")

# ================================================================= Cash flow
with tabs[1]:
    p2 = C.period_picker(ds, "fin_cf", default="Last 12 months", compare=False)
    cf = F.cash_flow_statement(ds, p2["start"], p2["end"])
    with st.container(horizontal=True):
        C.kpi("Opening cash", M(cf["opening_cash"]), help=f"Balance at the end of {(p2['start'] - pd.Timedelta(days=1)):%d %b %Y}.")
        C.kpi("Cash in", M(cf["inflows"]))
        C.kpi("Cash out", M(cf["outflows"]))
        C.kpi("Net change", M(cf["net_change"]))
        C.kpi("Closing cash", M(cf["closing_cash"]))
    ok = abs(cf["reconciliation_difference"]) < 0.01
    if ok:
        st.success(f"Opening + cash in − cash out = closing: difference {cf['reconciliation_difference']:.2f}", icon=":material/check_circle:")
    else:
        st.error(f"Cash does not reconcile: difference {cf['reconciliation_difference']:.2f}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Operating activities", M(cf["operating"]), help="Customer collections − refunds − supplier payments − operating expenses.", border=True)
    c2.metric("Investing activities", M(cf["investing"]), help="Capital expenditure.", border=True)
    c3.metric("Financing activities", M(cf["financing"]), help="Loans drawn/repaid, interest, owner investment and drawings.", border=True)
    bc = cf["by_category"].copy()
    fig = CH.bars(C.fx_df(bc.sort_values("amount"), ["amount"]), "category", {"Cash movement": "amount"}, "Cash movements by category (inflows positive)", horizontal=True, height=420)
    CH.show(fig, "Cash movements by category for the selected period", key="fin_cf_bars")
    C.table(bc[["activity", "category", "direction", "amount"]], money_cols=["amount"], name="cash_flow_by_category", alt="Cash movements by category")
    with st.expander("Independent cross-check of closing cash", icon=":material/fact_check:"):
        a, b = F.cash_balance_at(ds, p2["end"]), F.independent_cash_balance(ds, p2["end"])
        st.write(f"Closing cash from the cash ledger: **{money(a, cur, decimals=2)}**")
        st.write(f"Closing cash rebuilt directly from receipts, refunds, supplier payments, expenses, capex, loans and equity tables: **{money(b, cur, decimals=2)}**")
        st.write(f"Difference: {money(a - b, cur, decimals=2)}")
    monthly = ds.t["cash_ledger"].assign(month=lambda d: d["txn_date"].dt.to_period("M").dt.start_time)
    monthly = monthly[(monthly["txn_date"] >= p2["start"]) & (monthly["txn_date"] <= p2["end"])].pivot_table(index="month", columns="activity", values="amount", aggfunc="sum", fill_value=0.0).reset_index()
    for c in ["operating", "investing", "financing"]:
        if c not in monthly:
            monthly[c] = 0.0
    fig = CH.bars(C.fx_df(monthly, ["operating", "investing", "financing"]), "month", {"Operating": "operating", "Investing": "investing", "Financing": "financing"},
                  "Net cash movement by month and activity", stacked=True)
    CH.show(fig, "Monthly net cash movement by activity", key="fin_cf_month")

# ================================================================= Budget vs actual
with tabs[2]:
    p3 = C.period_picker(ds, "fin_bud", default="Last 12 months", compare=False)
    by = st.segmented_control("Group by", ["month", "store"], default="month", required=True, key="fin_bud_by", format_func=str.capitalize)
    bv = F.budget_vs_actual(ds, p3["start"], p3["end"], by=by)
    if bv.empty:
        C.empty_state("No budget lines for this period.")
    else:
        tot = bv[["b_net_sales", "a_net_sales", "b_operating_expenses", "a_operating_expenses", "b_op_profit", "a_op_profit"]].sum()
        with st.container(horizontal=True):
            C.kpi("Net sales vs budget", M(tot["a_net_sales"]), delta_text(tot["a_net_sales"], tot["b_net_sales"]), help=f"Budget {money(tot['b_net_sales'], cur, compact=True)}")
            C.kpi("Operating expenses vs budget", M(tot["a_operating_expenses"]), delta_text(tot["a_operating_expenses"], tot["b_operating_expenses"]), delta_color="inverse",
                  help=f"Budget {money(tot['b_operating_expenses'], cur, compact=True)}")
            C.kpi("Operating profit vs budget", M(tot["a_op_profit"]), delta_text(tot["a_op_profit"], tot["b_op_profit"]), help=f"Budget {money(tot['b_op_profit'], cur, compact=True)}")
        x = "month" if by == "month" else "store_id"
        if by == "month":
            fig = CH.bar_line(C.fx_df(bv, ["b_net_sales", "a_net_sales"]), "month", {"Budget net sales": "b_net_sales", "Actual net sales": "a_net_sales"}, {"Sales variance %": "sales_variance_pct"},
                              "Net sales: budget vs actual", line_is_pct=True)
            CH.show(fig, "Budget versus actual net sales by month with variance percentage", key="fin_bud_chart")
        C.table(bv, money_cols=["b_net_sales", "a_net_sales", "sales_variance", "b_cogs", "a_cogs", "cogs_variance", "b_operating_expenses", "a_operating_expenses", "opex_variance", "b_op_profit", "a_op_profit",
                                "op_profit_variance"], pct_cols=["sales_variance_pct"], name="budget_vs_actual", alt="Budget versus actual",
                columns=[c for c in [x, "b_net_sales", "a_net_sales", "sales_variance", "sales_variance_pct", "b_operating_expenses", "a_operating_expenses", "opex_variance", "b_op_profit", "a_op_profit", "op_profit_variance"] if c in bv],
                labels={"b_net_sales": "Budget sales", "a_net_sales": "Actual sales", "b_operating_expenses": "Budget opex", "a_operating_expenses": "Actual opex", "b_op_profit": "Budget op. profit", "a_op_profit": "Actual op. profit"})
        C.note("info", "Variance sign: positive is favourable to profit (sales above budget, costs below budget). 'HQ' rows hold shared head-office costs, which are budgeted but not allocated to stores.")

# ================================================================= Product & store profit
with tabs[3]:
    p4 = C.period_picker(ds, "fin_prod", default="Last 12 months", compare=False)
    view = st.segmented_control("Show", ["product", "category", "store"], default="product", required=True, key="fin_prof_view", format_func=str.capitalize)
    pt = F.profitability_table(ds, p4["start"], p4["end"], view)
    if pt.empty:
        C.empty_state("No sales in this period.")
    else:
        key_col = {"product": "product_name", "category": "category", "store": "store"}[view]
        fig = CH.scatter(C.fx_df(pt, ["gross_profit", "net_sales"]), "net_sales", "gross_profit", key_col, x_title=f"Net sales ({cur.code})", y_title=f"Gross profit ({cur.code})",
                         title=f"Net sales vs gross profit by {view}") if len(pt) > 1 else None
        if fig is not None:
            CH.show(fig, f"Scatter of net sales against gross profit by {view}", key="fin_prof_scatter")
        C.table(pt, money_cols=["net_sales", "cogs_net", "gross_profit", "variable_costs", "contribution", "discount", "refund"], pct_cols=["gross_margin", "contribution_margin"],
                count_cols=["units"], name=f"profit_by_{view}", alt=f"Profit by {view}")
        C.note("info", "Contribution = gross profit − variable selling costs (card fees, delivery, packaging) allocated to products by net sales within each store. "
                       "Rent, wages and other fixed costs are NOT allocated to products, so product 'contribution' is not product net profit.")
    st.subheader("Store operating profit (shared costs allocated)", anchor=False)
    alloc2 = st.segmented_control("Allocation rule for shared costs", ["net_sales", "equal"], default="net_sales", required=True, key="fin_alloc2",
                                  format_func=lambda x: {"net_sales": "Share of net sales", "equal": "Equal split"}[x])
    sp = F.store_profit_table(ds, p4["start"], p4["end"], alloc2)
    C.table(sp, columns=["store", "net_sales", "gross_profit", "gross_margin", "operating_expenses", "depreciation", "operating_profit", "operating_margin", "hq_share"],
            money_cols=["net_sales", "gross_profit", "operating_expenses", "depreciation", "operating_profit"], pct_cols=["gross_margin", "operating_margin", "hq_share"],
            labels={"hq_share": "Share of HQ costs"}, name="store_profit", alt="Store operating profit")
    whole = F.pnl(ds, p4["start"], p4["end"])
    st.caption(f"Reconciliation: store operating profits add up to {money(sp['operating_profit'].sum(), cur, decimals=0)}; whole-company operating profit is {money(whole['operating_profit'], cur, decimals=0)}.")

# ================================================================= Break-even
with tabs[4]:
    p5 = C.period_picker(ds, "fin_be", default="Last 12 months", compare=False)
    bs_stores = st.multiselect("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], placeholder="Whole company", key="fin_be_store")
    be = F.contribution_breakeven(ds, p5["start"], p5["end"], bs_stores or None)
    with st.container(horizontal=True):
        C.kpi("Contribution margin ratio", pct(be["contribution_margin_ratio"]), help="(Net sales − COGS − variable selling costs) ÷ net sales.")
        C.kpi("Fixed costs", M(be["fixed_costs"]), help="Fixed and discretionary operating expenses (incl. marketing) plus depreciation.")
        C.kpi("Break-even sales", M(be["break_even_sales"]) if be["break_even_sales"] is not None else "n/a", help="Fixed costs ÷ contribution margin ratio.")
        C.kpi("Actual net sales", M(be["net_sales"]))
        C.kpi("Margin of safety", pct(be["margin_of_safety"]) if be["margin_of_safety"] is not None else "n/a", help="How far sales can fall before operating profit reaches zero.")
    if be["note"]:
        st.warning(be["note"])
    elif be["break_even_per_day"] is not None:
        st.write(f"At this period's sales mix and costs the business needs about **{money(be['break_even_per_day'], cur, compact=True)} of net sales per day** "
                 f"({be['days']} days) to cover its fixed costs.")
        x = [0, be["net_sales"] * 1.4]
        fig = CH.lines(pd.DataFrame({"sales": [v * cur.rate for v in x],
                                     "Total cost": [(be["fixed_costs"] + v * (1 - be["contribution_margin_ratio"])) * cur.rate for v in x],
                                     "Revenue": [v * cur.rate for v in x]}), "sales", {"Revenue": "Revenue", "Total cost": "Total cost"}, "Break-even chart (revenue vs total cost)", money_axis=True,
                       dash={"Total cost": "dash"})
        fig.add_vline(x=be["break_even_sales"] * cur.rate, line_dash="dot", annotation_text="Break-even")
        fig.add_vline(x=be["net_sales"] * cur.rate, line_color="#009E73", annotation_text="Actual")
        CH.show(fig, "Break-even chart showing revenue, total cost, the break-even point and actual sales", key="fin_be_chart")
    C.definition("break-even", "**Variable costs** = cost of goods sold + card fees + delivery + packaging.  \n**Fixed costs** = every other operating expense (rent, wages, utilities, "
                               "software, insurance, marketing, repairs) + depreciation.  \n**Break-even sales** = fixed costs ÷ contribution-margin ratio, assuming today's product mix and price levels. "
                               "Marketing is treated as a fixed (period) cost although it can be changed at will.")

# ================================================================= Balance sheet
with tabs[5]:
    bs_date = st.date_input("Balance sheet date", value=ds.ref_date.date(), min_value=ds.opening_date.date(), max_value=ds.end.date(), key="fin_bs_date")
    bs = F.balance_sheet(ds, pd.Timestamp(bs_date))
    a, b = st.columns(2)
    with a:
        st.markdown("**Assets**")
        C.table(pd.DataFrame({"item": ["Cash", "Receivables", "Inventory", "Fixed assets (net book value)", "Total assets"],
                              "amount": [bs["cash"], bs["receivables"], bs["inventory"], bs["fixed_assets_net"], bs["total_assets"]]}), money_cols=["amount"], name="bs_assets", alt="Assets", download=False)
    with b:
        st.markdown("**Liabilities and equity**")
        C.table(pd.DataFrame({"item": ["Payables", "Loans (principal outstanding)", "Total liabilities", "Opening owner equity", "Owner investment − drawings", "Profit since the opening date (pre-tax)", "Total equity"],
                              "amount": [bs["payables"], bs["loans"], bs["total_liabilities"], bs["opening_equity"], bs["owner_net_investment"], bs["retained_profit"], bs["total_equity"]]}),
                money_cols=["amount"], name="bs_liabilities", alt="Liabilities and equity", download=False)
    if abs(bs["difference"]) < 0.01:
        st.success(f"Assets = liabilities + equity (difference {bs['difference']:.2f}). Every table in the dataset is consistent with every other.", icon=":material/check_circle:")
    else:
        st.error(f"The balance sheet does not balance: difference {bs['difference']:.2f}")
    C.definition("the balance sheet", "Opening balances (cash, receivables, inventory, net fixed assets, payables, loans and the balancing owner equity) are stored in the `opening_balances` table. "
                                      "Equity grows with pre-tax profit since the opening date and owner investment, and shrinks with drawings. No income tax is modelled.")
