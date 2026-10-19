import pandas as pd
import streamlit as st

from retailiq.analytics import actions as AC
from retailiq.analytics import capital as K
from retailiq.analytics import customers as CU
from retailiq.analytics import finance as F
from retailiq.analytics import reviews as RV
from retailiq.analytics.dataset import between
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import delta_text, money, pct, points_delta

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("How is the business doing right now, and where should you look next? "
         "Numbers are split into **measures over a period** (sales, profit, spend) and **balances at a date** (cash, stock, receivables).")

p = C.period_picker(ds, "ov", default="Last 3 months")
start, end = p["start"], p["end"]
cmp_ = p["cmp"]
cur_p = F.pnl(ds, start, end)
prev_p = F.pnl(ds, *cmp_) if cmp_ else None


def prev(field):
    return prev_p[field] if prev_p else None


# ---------------------------------------------------------------- marketing, repeat rate, ratings
def marketing_totals(a, b):
    daily = ds.t["campaign_daily"]
    spend = float(daily[between(daily["activity_date"], a, b)]["spend"].sum())
    tx = ds.t["sales_transactions"]
    attr = float(tx[tx["campaign_id"].notna() & between(tx["transaction_date"], a, b)]["net_amount"].sum())
    return spend, attr


mk_cur = marketing_totals(start, end)
mk_prev = marketing_totals(*cmp_) if cmp_ else (None, None)
rep_cur = CU.repeat_metrics(ds, end)["repeat_rate"]
rep_prev = CU.repeat_metrics(ds, cmp_[1])["repeat_rate"] if cmp_ and (cmp_[1] - pd.Timedelta(days=364)) >= ds.start else None
rv = cached.reviews(ds, key)
rv_cur = rv[between(rv["review_date"], start, end)]["rating"]
rv_prev = rv[between(rv["review_date"], *cmp_)]["rating"] if cmp_ else pd.Series(dtype=float)
rating_cur = float(rv_cur.mean()) if len(rv_cur) else None
rating_prev = float(rv_prev.mean()) if len(rv_prev) else None

cmp_text = f"vs {p['cmp_label']}" if cmp_ else "no comparison"

# ---------------------------------------------------------------- period measures
st.subheader("Measured over the selected period", anchor=False)
C.note("period", f"{p['label']} · change shown {cmp_text}")
with st.container(horizontal=True):
    C.kpi("Net sales", M(cur_p["net_sales"]), delta_text(cur_p["net_sales"], prev("net_sales")),
          help="Sales after discounts and returns, excluding GST. Never includes loans or owner investment.")
    C.kpi("Gross profit", M(cur_p["gross_profit"]), delta_text(cur_p["gross_profit"], prev("gross_profit")),
          help="Net sales minus cost of goods sold. This is NOT net profit: operating costs are not deducted yet.")
    C.kpi("Gross margin", pct(cur_p["gross_margin"]), points_delta(cur_p["gross_margin"], prev("gross_margin")), help="Gross profit ÷ net sales.")
    C.kpi("Operating profit", M(cur_p["operating_profit"]), delta_text(cur_p["operating_profit"], prev("operating_profit")),
          help="Gross profit minus operating expenses and depreciation. Interest and tax are not deducted.")
    C.kpi("Operating margin", pct(cur_p["operating_margin"]), points_delta(cur_p["operating_margin"], prev("operating_margin")),
          help="Operating profit ÷ net sales.")
with st.container(horizontal=True):
    C.kpi("Marketing spend", M(mk_cur[0]), delta_text(mk_cur[0], mk_prev[0]), delta_color="off", help="Media spend by campaign day within the period.")
    C.kpi("Attributed revenue", M(mk_cur[1]), delta_text(mk_cur[1], mk_prev[1]),
          help="Sales that redeemed a campaign code. Attribution is single-touch and does NOT prove the campaign caused the sale.")
    C.kpi("Repeat-purchase rate", pct(rep_cur), points_delta(rep_cur, rep_prev),
          help="Of identified customers who bought in the 12 months to the period end, the share who bought at least twice. Walk-in shoppers are excluded.")
    C.kpi("Average review rating", f"{rating_cur:.2f} / 5" if rating_cur else "n/a", f"{rating_cur - rating_prev:+.2f}" if rating_cur and rating_prev else None,
          help=f"Average star rating of {len(rv_cur):,} reviews written in the period.")

# ---------------------------------------------------------------- balances at a date
st.subheader(f"Balances at {end:%d %b %Y}", anchor=False)
snap = K.working_capital_snapshot(ds, end)
snap_prev = K.working_capital_snapshot(ds, cmp_[1]) if cmp_ else None
C.note("balance", "Balances are measured at one date, not over the period. Change shown against the comparison window's end date.")


def bdelta(field, invert=False):
    return delta_text(snap[field], snap_prev[field] if snap_prev else None)


with st.container(horizontal=True):
    C.kpi("Cash balance", M(snap["cash"]), bdelta("cash"), help="Opening cash plus all cash inflows minus outflows (see Finance > Cash flow).")
    C.kpi("Inventory value", M(snap["inventory"]), bdelta("inventory"), delta_color="off", help="Stock on hand at weighted-average cost.")
    C.kpi("Receivables", M(snap["receivables"]), bdelta("receivables"), delta_color="off", help="Sales invoiced but not yet collected, including card/UPI settlements in transit.")
    C.kpi("Payables", M(snap["payables"]), bdelta("payables"), delta_color="off", help="Supplier invoices received but not yet paid.")
    C.kpi("Working-capital requirement", M(snap["operating_working_capital"]), bdelta("operating_working_capital"), delta_color="off",
          help="Inventory + receivables − payables: the money tied up in running day-to-day operations.")
if snap["cash_conversion_cycle"] is not None:
    st.caption(f"Cash conversion cycle {snap['cash_conversion_cycle']:.1f} days = inventory {snap['inventory_days']:.1f} + receivable "
               f"{snap['receivable_days']:.1f} − payable {snap['payable_days']:.1f} days (trailing {snap['window_days']}-day flows).")
for n in snap["notes"]:
    C.note("warn", n)

# ---------------------------------------------------------------- charts
left, right = st.columns(2)
with left:
    with st.container(border=True):
        mp = F.monthly_pnl(ds, max(ds.start, end - pd.DateOffset(months=23)), end)
        mp = C.fx_df(mp[mp["full_month"]], ["net_sales", "gross_profit", "operating_profit"])
        fig = CH.bar_line(mp, "month", {"Net sales": "net_sales", "Operating profit": "operating_profit"}, {"Operating margin": "operating_margin"},
                          "Monthly net sales, operating profit and margin", line_is_pct=True)
        CH.show(fig, "Monthly net sales and operating profit bars with operating margin line", key="ov_monthly")
with right:
    with st.container(border=True):
        tr = K.working_capital_trend(ds)
        tr = tr[tr["date"] <= end]
        tr = C.fx_df(tr, ["cash", "inventory", "receivables", "payables", "operating_working_capital"])
        fig = CH.lines(tr, "date", {"Cash": "cash", "Inventory": "inventory", "Receivables": "receivables", "Payables": "payables"},
                       "Month-end balances")
        CH.show(fig, "Month-end cash, inventory, receivables and payables", key="ov_balances")

c1, c2 = st.columns(2)
with c1:
    with st.container(border=True):
        d = ds.daily[between(ds.daily["date"], start, end)]
        cat = d.assign(net_sales=d["net"] - d["refund"]).groupby("category", as_index=False)["net_sales"].sum().sort_values("net_sales", ascending=False)
        fig = CH.bars(C.fx_df(cat, ["net_sales"]), "category", {"Net sales": "net_sales"}, "Net sales by category (selected period)", horizontal=True, height=300)
        CH.show(fig, "Net sales by product category for the selected period", key="ov_cat")
with c2:
    with st.container(border=True):
        st.markdown("**Top findings from the Action center**")
        findings = [f for f in cached.action_findings(ds, key, AC.Settings()) if f.triggered][:4]
        if not findings:
            st.success("No rule is currently triggered.")
        for f in findings:
            icon = {"High": ":red[:material/error:]", "Medium": ":orange[:material/warning:]", "Low": ":blue[:material/info:]"}.get(f.severity, ":material/info:")
            st.markdown(f"{icon} **{f.title}**")
            st.caption(f"{f.area} · " + "; ".join(f"{k}: {v}" for k, v in list(f.numbers.items())[:2]))
        st.page_link("app_pages/action_center.py", label="See all findings with their rules and numbers", icon=":material/arrow_forward:")

st.subheader("Supporting detail", anchor=False)
with st.container(horizontal=True):
    st.page_link("app_pages/finance.py", label="Profit & cash detail", icon=":material/account_balance:")
    st.page_link("app_pages/working_capital.py", label="Working capital", icon=":material/water_drop:")
    st.page_link("app_pages/inventory.py", label="Stock & reordering", icon=":material/inventory_2:")
    st.page_link("app_pages/cash_funding.py", label="13-week cash & funding", icon=":material/payments:")
    st.page_link("app_pages/marketing.py", label="Campaigns", icon=":material/campaign:")
    st.page_link("app_pages/customers.py", label="Customers", icon=":material/groups:")
    st.page_link("app_pages/reviews.py", label="Reviews", icon=":material/reviews:")

with st.expander("Export the numbers on this page", icon=":material/download:"):
    export = pd.DataFrame({
        "metric": ["Net sales", "Gross profit", "Gross margin", "Operating profit", "Operating margin", "Marketing spend", "Attributed revenue",
                   "Repeat-purchase rate", "Average review rating", "Cash", "Inventory", "Receivables", "Payables", "Working-capital requirement"],
        "type": ["period"] * 9 + ["balance"] * 5,
        "value": [cur_p["net_sales"], cur_p["gross_profit"], cur_p["gross_margin"], cur_p["operating_profit"], cur_p["operating_margin"], mk_cur[0], mk_cur[1],
                  rep_cur, rating_cur, snap["cash"], snap["inventory"], snap["receivables"], snap["payables"], snap["operating_working_capital"]],
        "comparison_value": [prev("net_sales"), prev("gross_profit"), prev("gross_margin"), prev("operating_profit"), prev("operating_margin"), mk_prev[0],
                             mk_prev[1], rep_prev, rating_prev] + [snap_prev[f] if snap_prev else None for f in
                                                                    ["cash", "inventory", "receivables", "payables", "operating_working_capital"]],
    })
    st.caption(f"Amounts in INR (base currency). Period {p['label']}; comparison {p['cmp_label'] or 'none'}.")
    st.dataframe(export, hide_index=True, alt="Overview metrics export")
    st.download_button("Download overview CSV", export.to_csv(index=False).encode("utf-8-sig"), "retailiq_overview.csv", "text/csv", icon=":material/download:")
