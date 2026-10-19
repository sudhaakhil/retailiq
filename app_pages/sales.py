import pandas as pd
import streamlit as st

from retailiq.analytics.accounting import safe_div
from retailiq.analytics.dataset import between
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui import state
from retailiq.ui.formatting import delta_text, money, pct, points_delta

ds = state.get_dataset()
cur = state.currency()
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("Are sales growing, and where do they come from? Filters apply to every number and chart below.")
p = C.period_picker(ds, "sales", default="Last 12 months")

with st.container(horizontal=True):
    stores = st.multiselect("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], placeholder="All stores", key="sales_store")
    cats = st.multiselect("Category", sorted(ds.t["products"]["category"].unique()), placeholder="All categories", key="sales_cat")
    chans = st.multiselect("Channel", ["in_store", "online", "business"], placeholder="All channels", key="sales_chan")
stores, cats, chans = stores or ds.store_ids, cats or sorted(ds.t["products"]["category"].unique()), chans or ["in_store", "online", "business"]


def slice_(a, b):
    L = ds.lines
    L = L[between(L["transaction_date"], a, b) & L["store_id"].isin(stores) & L["category"].isin(cats) & L["channel"].isin(chans)]
    R = ds.ret
    R = R[between(R["return_date"], a, b) & R["store_id"].isin(stores) & R["category"].isin(cats) & R["channel"].isin(chans)]
    return L, R


def summary(L, R):
    gross, disc, net = L["gross_amount"].sum(), L["discount_amount"].sum(), L["net_amount"].sum()
    refunds = R["refund_amount"].sum()
    cogs = L["cogs_amount"].sum() - R["cogs_reversal"].sum()
    ns = net - refunds
    return {"gross": gross, "discount": disc, "refunds": refunds, "net_sales": ns, "orders": L["transaction_id"].nunique(), "units": L["quantity"].sum() - R["quantity"].sum(),
            "aov": safe_div(net, L["transaction_id"].nunique()), "gp": ns - cogs, "gm": safe_div(ns - cogs, ns), "discount_rate": safe_div(disc, gross),
            "return_rate": safe_div(refunds, net)}


L, R = slice_(p["start"], p["end"])
if L.empty:
    C.empty_state("No sales match these filters in the selected period. Try a longer period or clear a filter.")
    st.stop()
s = summary(L, R)
s0 = summary(*slice_(*p["cmp"])) if p["cmp"] else None
g = lambda k: s0[k] if s0 else None  # noqa: E731

with st.container(horizontal=True):
    C.kpi("Net sales", M(s["net_sales"]), delta_text(s["net_sales"], g("net_sales")), help="After discounts and returns, excluding GST.")
    C.kpi("Orders", f"{s['orders']:,}", delta_text(s["orders"], g("orders")), help="Number of sales transactions (baskets).")
    C.kpi("Average order value", money(s["aov"], cur), delta_text(s["aov"], g("aov")), help="Sales after discounts (before returns) ÷ orders.")
    C.kpi("Units sold (net of returns)", f"{s['units']:,.0f}", delta_text(s["units"], g("units")))
    C.kpi("Gross margin", pct(s["gm"]), points_delta(s["gm"], g("gm")))
    C.kpi("Discount rate", pct(s["discount_rate"]), points_delta(s["discount_rate"], g("discount_rate")), delta_color="inverse", help="Discounts ÷ gross sales.")
    C.kpi("Return rate", pct(s["return_rate"]), points_delta(s["return_rate"], g("return_rate")), delta_color="inverse", help="Refunds ÷ sales after discounts.")

# ---------------------------------------------------------------- trend
with st.container(border=True):
    gran = st.segmented_control("Trend by", ["Day", "Week", "Month"], default="Week" if (p["end"] - p["start"]).days < 400 else "Month", required=True, key="sales_gran")
    metric = st.segmented_control("Measure", ["Net sales", "Orders", "Units"], default="Net sales", required=True, key="sales_metric")
    freq = {"Day": "D", "Week": "W-SUN", "Month": "MS"}[gran]

    def series(L_, R_, shift=None):
        a = L_.assign(period=L_["transaction_date"]).groupby(pd.Grouper(key="period", freq=freq)).agg(
            net=("net_amount", "sum"), orders=("transaction_id", "nunique"), units=("quantity", "sum"))
        r = R_.groupby(pd.Grouper(key="return_date", freq=freq)).agg(refund=("refund_amount", "sum"), ret_units=("quantity", "sum"))
        a = a.join(r, how="left").fillna(0.0)
        out = pd.DataFrame({"date": a.index, "Net sales": a["net"] - a["refund"], "Orders": a["orders"], "Units": a["units"] - a["ret_units"]})
        if shift is not None:
            out["date"] = out["date"] + shift
        return out

    cur_s = series(L, R)
    plot = C.fx_df(cur_s, ["Net sales"])
    ser = {"This period": metric}
    plot["This period"] = plot[metric]
    if p["cmp"]:
        L0, R0 = slice_(*p["cmp"])
        sh = p["start"] - p["cmp"][0]
        c0 = C.fx_df(series(L0, R0, sh), ["Net sales"])
        plot = plot.merge(c0[["date", metric]].rename(columns={metric: "Comparison"}), on="date", how="outer").sort_values("date")
        ser["Comparison (aligned)"] = "Comparison"
    fig = CH.lines(plot, "date", {k: ("This period" if k == "This period" else "Comparison") for k in ser}, f"{metric} by {gran.lower()}", money_axis=metric == "Net sales",
                   dash={"Comparison (aligned)": "dot"})
    CH.show(fig, f"{metric} by {gran.lower()} for the selected period with comparison", key="sales_trend")

# ---------------------------------------------------------------- breakdowns
tab1, tab2, tab3, tab4 = st.tabs(["By product", "By category and store", "Channel and payment", "Supporting records"])
with tab1:
    pr = L.groupby(["product_id", "product_name", "category"], as_index=False).agg(
        units=("quantity", "sum"), gross=("gross_amount", "sum"), discount=("discount_amount", "sum"), net_amount=("net_amount", "sum"), cogs=("cogs_amount", "sum"))
    rf = R.groupby("product_id", as_index=False).agg(refund=("refund_amount", "sum"), cogs_rev=("cogs_reversal", "sum"), ret_units=("quantity", "sum"))
    pr = pr.merge(rf, on="product_id", how="left").fillna(0.0)
    pr["net_sales"] = pr["net_amount"] - pr["refund"]
    pr["gross_profit"] = pr["net_sales"] - (pr["cogs"] - pr["cogs_rev"])
    pr["gross_margin"] = [safe_div(a, b) for a, b in zip(pr["gross_profit"], pr["net_sales"])]
    pr["share_of_sales"] = pr["net_sales"] / pr["net_sales"].sum()
    pr["return_rate"] = [safe_div(a, b) for a, b in zip(pr["refund"], pr["net_amount"])]
    pr = pr.sort_values("net_sales", ascending=False)
    fig = CH.bars(C.fx_df(pr.head(12), ["net_sales"]), "product_name", {"Net sales": "net_sales"}, "Top products by net sales", horizontal=True, height=380)
    CH.show(fig, "Top products by net sales", key="sales_prod")
    C.table(pr, columns=["product_id", "product_name", "category", "units", "net_sales", "share_of_sales", "gross_profit", "gross_margin", "discount", "return_rate"],
            money_cols=["net_sales", "gross_profit", "discount"], pct_cols=["share_of_sales", "gross_margin", "return_rate"], count_cols=["units"],
            name="sales_by_product", alt="Sales by product")
with tab2:
    a, b = st.columns(2)
    with a:
        cg = L.groupby("category", as_index=False).agg(net_amount=("net_amount", "sum"), cogs=("cogs_amount", "sum"))
        rc = R.groupby("category", as_index=False).agg(refund=("refund_amount", "sum"), cogs_rev=("cogs_reversal", "sum"))
        cg = cg.merge(rc, on="category", how="left").fillna(0.0)
        cg["net_sales"] = cg["net_amount"] - cg["refund"]
        cg["gross_profit"] = cg["net_sales"] - (cg["cogs"] - cg["cogs_rev"])
        cg["gross_margin"] = [safe_div(x, y) for x, y in zip(cg["gross_profit"], cg["net_sales"])]
        C.table(cg.sort_values("net_sales", ascending=False), columns=["category", "net_sales", "gross_profit", "gross_margin"], money_cols=["net_sales", "gross_profit"],
                pct_cols=["gross_margin"], name="sales_by_category", alt="Sales by category")
    with b:
        sg = L.groupby("store_id", as_index=False).agg(net_amount=("net_amount", "sum"), cogs=("cogs_amount", "sum"), orders=("transaction_id", "nunique"))
        rs = R.groupby("store_id", as_index=False).agg(refund=("refund_amount", "sum"), cogs_rev=("cogs_reversal", "sum"))
        sg = sg.merge(rs, on="store_id", how="left").fillna(0.0)
        sg["store"] = sg["store_id"].map(ds.store_names)
        sg["net_sales"] = sg["net_amount"] - sg["refund"]
        sg["gross_profit"] = sg["net_sales"] - (sg["cogs"] - sg["cogs_rev"])
        sg["gross_margin"] = [safe_div(x, y) for x, y in zip(sg["gross_profit"], sg["net_sales"])]
        C.table(sg, columns=["store", "orders", "net_sales", "gross_profit", "gross_margin"], money_cols=["net_sales", "gross_profit"], pct_cols=["gross_margin"],
                count_cols=["orders"], name="sales_by_store", alt="Sales by store")
    C.note("info", "Gross profit by store is before head-office costs. See Finance > Store profit for allocated operating profit.")
with tab3:
    a, b = st.columns(2)
    ch = L.groupby("channel", as_index=False)["net_amount"].sum()
    pm = L.groupby("payment_method", as_index=False)["net_amount"].sum()
    with a:
        CH.show(CH.donut(ch["channel"], ch["net_amount"], "Sales by channel"), "Share of sales by channel", key="sales_chan_donut")
    with b:
        CH.show(CH.donut(pm["payment_method"], pm["net_amount"], "Sales by payment method"), "Share of sales by payment method", key="sales_pay_donut")
    C.note("info", "Credit sales (business customers) are collected after 30 days or more, which raises receivables - see Working capital.")
with tab4:
    st.caption("Every figure above is built from these transaction rows (sales after discounts, before returns). Search by transaction, customer or product ID.")
    q = st.text_input("Search transaction ID / customer ID / product ID", key="sales_q", placeholder="e.g. T0001234, C0042 or P05")
    tx = L.groupby(["transaction_id", "transaction_date", "store_id", "customer_id", "channel", "payment_method", "campaign_id"], dropna=False, as_index=False).agg(
        lines=("line_id", "count"), units=("quantity", "sum"), gross_amount=("gross_amount", "sum"), discount_amount=("discount_amount", "sum"), net_amount=("net_amount", "sum"))
    if q:
        L2 = L[L["product_id"].str.contains(q, case=False)]["transaction_id"] if q.upper().startswith("P") else pd.Series(dtype=str)
        tx = tx[tx["transaction_id"].str.contains(q, case=False) | tx["customer_id"].fillna("").str.contains(q, case=False) | tx["transaction_id"].isin(L2)]
    st.caption(f"{len(tx):,} transactions" + (" (showing the latest 1,000; the download has all)" if len(tx) > 1000 else ""))
    C.table(tx.sort_values("transaction_date", ascending=False).head(1000), money_cols=["gross_amount", "discount_amount", "net_amount"], count_cols=["lines", "units"],
            name="sales_transactions", alt="Sales transactions", download=False, money_decimals=2)
    st.download_button("Download all matching transactions (CSV)", tx.to_csv(index=False).encode("utf-8-sig"), "retailiq_transactions.csv", "text/csv", icon=":material/download:")
