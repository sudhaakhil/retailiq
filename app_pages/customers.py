import pandas as pd
import streamlit as st

from retailiq.analytics import customers as CU
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import delta_text, money, pct, points_delta

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("Which customers return, and which might be drifting away? Only **identified** customers (loyalty and business accounts) can be tracked; "
         "anonymous walk-in baskets are excluded from these figures.")
ctype = st.segmented_control("Customer type", ["retail", "business", "all"], default="retail", required=True, key="cu_type",
                             format_func=lambda x: {"retail": "Retail (loyalty)", "business": "Business accounts", "all": "All"}[x])
ct = None if ctype == "all" else ctype
rm = CU.repeat_metrics(ds, ds.ref_date)
prev_end = ds.ref_date - pd.Timedelta(days=365)
rm0 = CU.repeat_metrics(ds, prev_end) if prev_end - pd.Timedelta(days=364) >= ds.start else None
C.note("period", f"Window: 12 months to {rm['window_end']:%d %b %Y}. Walk-in (anonymous) sales are {pct(1 - rm['identified_revenue_share'])} of revenue and cannot be tracked.")
with st.container(horizontal=True):
    g = lambda k: rm0[k] if rm0 else None  # noqa: E731
    C.kpi("Active identified customers", f"{rm['active_customers']:,}", delta_text(rm["active_customers"], g("active_customers")), help="Customers with at least one purchase in the window.")
    C.kpi("Repeat-purchase rate", pct(rm["repeat_rate"]), points_delta(rm["repeat_rate"], g("repeat_rate")), help="Customers with ≥ 2 orders ÷ customers with ≥ 1 order in the window.")
    C.kpi("Purchase frequency", f"{rm['purchase_frequency']:.1f} orders" if rm["purchase_frequency"] else "n/a", help="Orders per active customer in the window.")
    C.kpi("Average order value", money(rm["avg_order_value"], cur), delta_text(rm["avg_order_value"], g("avg_order_value")), help="Sales after discounts ÷ orders (identified customers).")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["New vs returning", "RFM segments", "Cohort retention", "Becoming inactive", "Lifetime value"])

with tab1:
    nv = CU.new_vs_returning(ds, customer_type=ct)
    if nv.empty:
        C.empty_state("No identified purchases for this selection.")
    else:
        fig = CH.bars(nv, "month", {"Returning customers": "returning_customers", "New customers": "new_customers"}, "Active customers per month", money_axis=False, stacked=True, y_title="Customers")
        CH.show(fig, "Active customers per month split into new and returning", key="cu_nv")
        C.table(nv, count_cols=["active_customers", "new_customers", "returning_customers", "orders"], money_cols=["net_sales", "avg_order_value"], dec_cols=["orders_per_customer"],
                name="new_vs_returning", alt="New and returning customers by month")
        C.definition("new vs returning", "A customer is **new** in the month of their first purchase *if they registered on or after the dataset start*. Customers who registered earlier are always "
                                         "**returning** because their earlier history is not in the data.")

with tab2:
    c1, c2 = st.columns(2)
    at_risk = c1.number_input("'Becoming inactive' after (days without a purchase)", 14, 365, 60, 10, key="cu_atrisk")
    inactive = c2.number_input("'Inactive' after (days)", int(at_risk) + 1, 720, max(120, int(at_risk) + 1), 10, key="cu_inactive")
    rfm = cached.rfm(ds, key, 365, int(at_risk), int(inactive), ct)
    if rfm.empty:
        C.empty_state("No customers for this selection.")
        st.stop()
    seg = rfm.groupby("segment", as_index=False).agg(customers=("customer_id", "count"), spend=("monetary", "sum"), avg_recency=("recency_days", "mean"), avg_orders=("frequency", "mean"))
    seg["share_of_spend"] = seg["spend"] / seg["spend"].sum() if seg["spend"].sum() else 0.0
    seg = seg.sort_values("spend", ascending=False)
    a, b = st.columns(2)
    with a:
        CH.show(CH.bars(C.fx_df(seg, ["spend"]), "segment", {"Spend in last 12 months": "spend"}, "Spend by RFM segment", horizontal=True, height=330), "Spend by RFM segment", key="cu_seg")
    with b:
        C.table(seg, money_cols=["spend"], pct_cols=["share_of_spend"], count_cols=["customers"], dec_cols=["avg_recency", "avg_orders"], labels={"avg_recency": "Avg days since purchase", "avg_orders": "Avg orders"},
                name="rfm_segments", alt="RFM segment summary")
    q = st.text_input("Search customers (ID or name)", key="cu_q")
    pick = st.multiselect("Segment", sorted(rfm["segment"].unique()), placeholder="All segments", key="cu_seg_pick")
    view = rfm[rfm["segment"].isin(pick or rfm["segment"].unique())]
    if q:
        view = view[view["customer_id"].str.contains(q, case=False) | view["customer_name"].str.contains(q, case=False)]
    C.table(view, columns=["customer_id", "customer_name", "segment", "status", "recency_days", "frequency", "monetary", "R", "F", "M"], money_cols=["monetary"], count_cols=["recency_days", "frequency", "R", "F", "M"],
            labels={"monetary": "Spend (12 mo)", "frequency": "Orders (12 mo)", "recency_days": "Days since last purchase"}, name="rfm_customers", alt="Customer RFM scores", height=360)
    C.definition("RFM", "**Recency** = days since the last purchase; **Frequency** = orders in the last 12 months; **Monetary** = spend after returns in the last 12 months. Each is scored 1-5 by rank among customers "
                        "who bought in the window (5 = best). Segments: *Champions* R≥4 & F≥4; *Loyal* R≥3 & F≥3; *New or promising* R≥4 & F≤2; *At risk* R≤2 & F≥4; *Hibernating* R≤2; otherwise *Needs attention*; "
                        "customers with no purchase in the window are *Lapsed*.")

with tab3:
    coh = cached.cohorts(ds, key, ct)
    if coh.empty:
        C.empty_state("No cohort data for this selection.")
    else:
        mat = coh.drop(columns="cohort_size")
        st.caption("Share of each monthly cohort that purchased k months after their first purchase. Blank = not yet observable. Only customers registered during the dataset are included.")
        CH.show(CH.heatmap(mat.iloc[:, :13], "Cohort retention (months since first purchase)"), "Cohort retention heatmap by acquisition month", key="cu_cohort")
        tbl = coh.reset_index(names="cohort").astype({"cohort": str})
        tbl.columns = [str(c) for c in tbl.columns]
        month_cols = [c for c in tbl.columns if c not in ("cohort", "cohort_size")]
        C.table(tbl, pct_cols=month_cols, count_cols=["cohort_size"], name="cohort_retention", alt="Cohort retention table", labels={c: f"Month {c}" for c in month_cols})

with tab4:
    st.markdown("Rule: **Active** < your at-risk limit; **Becoming inactive** between the limits; **Inactive** at or above the inactive limit (days since last purchase). Limits are set on the RFM tab.")
    summ = CU.inactivity_summary(rfm)
    C.table(summ, money_cols=["spend_in_window"], count_cols=["customers"], dec_cols=["avg_recency_days"], name="inactivity_summary", alt="Customers by activity status", download=False)
    risky = rfm[(rfm["status"] == "Becoming inactive")].sort_values("monetary", ascending=False)
    st.markdown(f"**{len(risky):,} customers are becoming inactive** (spend over the last 12 months: {money(risky['monetary'].sum(), cur, compact=True)}). Highest-value first:")
    C.table(risky, columns=["customer_id", "customer_name", "recency_days", "frequency", "monetary", "segment"], money_cols=["monetary"], count_cols=["recency_days", "frequency"], name="becoming_inactive",
            alt="Customers becoming inactive", height=360)
    C.note("info", "A fixed-day rule is simple and transparent but crude: some customers naturally buy every 3 months. Compare with each customer's usual gap before acting.")

with tab5:
    lv = CU.lifetime_value(ds)
    if not lv.get("valid"):
        st.warning(lv.get("reason", "Lifetime value is not available."))
    else:
        with st.container(horizontal=True):
            C.kpi("Orders per customer per year", f"{lv['orders_per_year']:.1f}")
            C.kpi("Average order value", money(lv["avg_order_value"], cur))
            C.kpi("Gross margin on orders", pct(lv["gross_margin"]))
            C.kpi("Annual retention", pct(lv["annual_retention"]), help=f"Of {lv['customers_in_retention_base']:,} customers active 13-24 months ago.")
            C.kpi("Expected lifetime", f"{lv['expected_lifetime_years']:.1f} years" + (" (capped)" if lv["capped"] else ""))
            C.kpi("Lifetime value (gross profit)", M(lv["clv"]), help="Margin per year × expected lifetime. Undiscounted, before acquisition cost.")
        st.markdown(f"**Method.** CLV = (orders per year × average order value × gross margin) × expected lifetime, where expected lifetime = 1 ÷ (1 − annual retention), capped at 5 years. "
                    f"Observation period: {lv['window'][0]:%d %b %Y} to {lv['window'][1]:%d %b %Y} for the current year, and the 12 months before it for retention.")
        st.warning("**Limitations:** assumes retention and margin stay constant; ignores discounting, acquisition cost and customers who return after a long gap; needs 24 months of history; "
                   "and the data is simulated, so this illustrates the method and is not a statement about real customer value.", icon=":material/warning:")
