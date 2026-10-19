import pandas as pd
import streamlit as st

from retailiq.analytics import marketing as MK
from retailiq.analytics.accounting import safe_div
from retailiq.analytics.dataset import between
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import money, pct

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("Which campaigns produce useful results? **ROAS is not profit**: it ignores what the products cost, how deep the discount was and the costs of selling. "
         "This page shows both, and is explicit that attribution is *not* proof of cause.")
p = C.period_picker(ds, "mk", default="Last 12 months", compare=False)
baseline_share = st.slider("Share of attributed sales assumed to have happened anyway (%)", 0, 80, 25, 5, key="mk_baseline",
                           help="Assumption used only for the 'adjusted contribution' column. 0% treats every attributed sale as caused by the campaign.") / 100
perf = cached.campaigns(ds, key, p["start"], p["end"], baseline_share)
if perf.empty:
    C.empty_state("No campaigns had spend in this period. Choose a longer period.")
    st.stop()
tot = perf[["spend", "attributed_revenue", "contribution", "adjusted_contribution", "orders", "clicks", "impressions"]].sum()
share_total = MK.attribution_check(ds)  # whole history, independent of the period filter

with st.container(horizontal=True):
    C.kpi("Marketing spend", M(tot["spend"]))
    C.kpi("Attributed revenue", M(tot["attributed_revenue"]), help="After discounts, before returns. Each sale is attributed to at most one campaign.")
    C.kpi("ROAS", f"{safe_div(tot['attributed_revenue'], tot['spend']):.2f}×" if safe_div(tot["attributed_revenue"], tot["spend"]) is not None else "n/a", help="Attributed revenue ÷ spend.")
    C.kpi("Contribution after costs", M(tot["contribution"]), help="Attributed revenue − refunds − COGS − estimated selling costs − media spend. Can be negative even when ROAS looks strong.")
    C.kpi(f"Adjusted contribution ({int(baseline_share * 100)}% would have happened anyway)", M(tot["adjusted_contribution"]), delta_color="off",
          help="Removes the assumed non-incremental share and charges the discounts given on it.")
st.caption(f"Attributed revenue is {pct(share_total['attributed_share'])} of company revenue over the whole dataset (a sale carries at most one campaign, so it is never double counted).")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Campaigns", "Channels", "Budget scenario", "Attribution method", "Link to operations"])

with tab1:
    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            fig = CH.scatter(C.fx_df(perf, ["contribution", "spend"]), "roas", "contribution", "campaign_name", size="spend", title="ROAS vs contribution (bubble = spend)",
                             x_title="ROAS (×)", y_title=f"Contribution ({cur.code})", hline=0)
            CH.show(fig, "Scatter of ROAS against contribution for each campaign, sized by spend", key="mk_scatter")
    with c2:
        with st.container(border=True):
            fig = CH.bars(C.fx_df(perf.sort_values("contribution"), ["contribution", "adjusted_contribution"]), "campaign_name",
                          {"Contribution": "contribution", "Adjusted contribution": "adjusted_contribution"}, "Contribution by campaign", horizontal=True, height=380)
            CH.show(fig, "Contribution and adjusted contribution by campaign", key="mk_contrib")
    C.table(perf, columns=["campaign_id", "campaign_name", "channel", "spend", "impressions", "clicks", "ctr", "cpc", "orders", "conversion_rate", "cpa", "new_customers", "cac", "attributed_revenue", "roas",
                           "discount_rate", "contribution", "adjusted_contribution", "conversion_rate_flag"],
            money_cols=["spend", "cpc", "cpa", "cac", "attributed_revenue", "contribution", "adjusted_contribution"], pct_cols=["ctr", "conversion_rate", "discount_rate"],
            count_cols=["impressions", "clicks", "orders", "new_customers"], dec_cols=["roas"], money_decimals=0,
            labels={"ctr": "CTR", "cpc": "Cost per click", "conversion_rate": "Conversion rate (orders ÷ clicks)", "cpa": "Cost per attributed order", "cac": "Cost per new customer",
                    "roas": "ROAS", "conversion_rate_flag": "Note"}, name="campaign_performance", alt="Campaign performance")
    C.definition("the campaign metrics", "- **CTR** = clicks ÷ impressions.  **Cost per click** = spend ÷ clicks.\n"
                                         "- **Conversion rate** = attributed orders ÷ **clicks** (the denominator is clicks, not visitors). It can exceed 100% for offline channels (print/QR) where redemptions are not all click-driven.\n"
                                         "- **Cost per attributed order** = spend ÷ attributed orders.  **Cost per new customer** = spend ÷ customers whose first-ever purchase was attributed to the campaign.\n"
                                         "- **ROAS** = attributed revenue ÷ spend.  **Contribution** = attributed revenue − refunds − cost of goods sold (net of returns) − estimated variable selling costs − media spend.\n"
                                         "- Variable selling costs use the company's actual ratio of card fees + delivery + packaging to sales over the period.")
    weak = perf[(perf["roas"].fillna(0) >= 3) & (perf["contribution_per_rupee_spent"].fillna(0) < 0.1)]
    if len(weak):
        st.warning("**Strong ROAS, weak profit:** " + "; ".join(f"{r.campaign_name} (ROAS {r.roas:.1f}×, discount {pct(r.discount_rate)}, contribution {money(r.contribution, cur, compact=True)})" for r in weak.itertuples()), icon=":material/warning:")

with tab2:
    ch = MK.channel_summary(perf)
    fig = CH.bars(C.fx_df(ch, ["spend", "contribution"]), "channel", {"Spend": "spend", "Contribution": "contribution"}, "Spend and contribution by channel")
    CH.show(fig, "Spend and contribution by marketing channel", key="mk_channel")
    C.table(ch, columns=["channel", "campaigns", "spend", "impressions", "clicks", "ctr", "cpc", "orders", "cpa", "attributed_revenue", "roas", "contribution", "contribution_per_rupee_spent"],
            money_cols=["spend", "cpc", "cpa", "attributed_revenue", "contribution"], pct_cols=["ctr"], count_cols=["campaigns", "impressions", "clicks", "orders"],
            dec_cols=["roas", "contribution_per_rupee_spent"], labels={"contribution_per_rupee_spent": "Contribution per rupee spent"}, name="channel_summary", alt="Channel summary")
    C.note("info", "Channel ratios are recomputed from channel totals (spend ÷ clicks etc.), not averaged across campaigns.")

with tab3:
    st.markdown("Change next period's budget by channel. Revenue is assumed to respond with **diminishing returns**: new revenue = revenue × (new spend ÷ old spend)^elasticity.")
    elasticity = st.slider("Response elasticity (assumption)", 0.0, 1.2, 0.6, 0.05, key="mk_elast",
                           help="1.0 = revenue grows in step with spend; 0.5 = doubling spend adds about 41% revenue; 0 = spend has no effect. This is an input, not a measured value.")
    cols = st.columns(len(ch))
    changes = {}
    for col, r in zip(cols, ch.itertuples()):
        changes[r.channel] = col.number_input(f"{r.channel} (%)", -90.0, 300.0, 0.0, 10.0, key=f"mk_chg_{r.channel}") / 100
    try:
        sc = MK.marketing_budget_scenario(perf, changes, elasticity, baseline_share)
        total_new = sc["new_spend"].sum()
        with st.container(horizontal=True):
            C.kpi("Spend", M(total_new), f"{(total_new - sc['current_spend'].sum()) / sc['current_spend'].sum() * 100:+.1f}%" if sc["current_spend"].sum() else None, delta_color="off")
            C.kpi("Expected attributed revenue", M(sc["expected_attributed_revenue"].sum()), f"{money(sc['revenue_change'].sum(), cur, compact=True)}", delta_color="off")
            C.kpi("Expected contribution", M(sc["expected_contribution"].sum()), f"{money(sc['expected_contribution'].sum() - sc['current_contribution'].sum(), cur, compact=True)} vs current")
        C.table(sc, money_cols=["current_spend", "new_spend", "current_attributed_revenue", "expected_attributed_revenue", "revenue_change", "current_contribution", "expected_contribution"],
                dec_cols=["marginal_roas", "new_roas"], labels={"marginal_roas": "Marginal ROAS (extra revenue ÷ extra spend)", "new_roas": "New ROAS"}, name="marketing_budget_scenario", alt="Marketing budget scenario")
        C.note("warn", "Assumption-based. The response curve is not estimated from data, and more spend also needs more stock and working capital - see Connected scenario.")
        st.page_link("app_pages/integrated_scenario.py", label="Follow the chain from spend to stock to cash", icon=":material/account_tree:")
    except ValueError as exc:
        st.error(str(exc))

with tab4:
    chk = MK.attribution_check(ds)  # whole history, independent of the period filter
    st.markdown("""**How attribution works in this simulated dataset**

Every sale carries at most **one** campaign code - the one the shopper redeemed at the till or online. When a campaign is running, a share of eligible baskets redeems it (the `redemption` rate differs by campaign).
That is *single-touch, redemption-based* attribution:

- A sale is counted for the campaign **only if the code was used**; people who saw an advert but did not use a code are not counted.
- Some attributed shoppers would have bought anyway. The *adjusted contribution* uses the share you choose above to remove them.
- Attribution **does not prove** the campaign caused the sale, and the numbers describe simulated shoppers, not real behaviour.""")
    with st.container(horizontal=True):
        C.kpi("Company revenue (all time)", M(chk["total_revenue"]))
        C.kpi("Attributed (by transactions)", M(chk["attributed_by_transactions"]))
        C.kpi("Attributed (campaign table)", M(chk["attributed_by_campaign_table"]))
        C.kpi("Difference", f"{chk['difference']:.2f}")
        C.kpi("Max campaigns per sale", f"{chk['max_campaigns_per_transaction']}")
    if chk["within_total"] and abs(chk["difference"]) < 0.01 and chk["max_campaigns_per_transaction"] <= 1:
        st.success("Check passed: attributed revenue never exceeds company revenue, and each sale belongs to at most one campaign.", icon=":material/check_circle:")
    else:
        st.error("Attribution check failed.")
    sel = st.selectbox("Inspect the attributed sales of a campaign", perf["campaign_id"], format_func=lambda c: f"{c} · {perf.set_index('campaign_id').loc[c, 'campaign_name']}", key="mk_inspect")
    tx = MK.attributed_transactions(ds, p["start"], p["end"])
    tx = tx[tx["campaign_id"] == sel]
    C.table(tx.sort_values("transaction_date"), columns=["transaction_id", "transaction_date", "store_id", "customer_id", "channel", "gross_amount", "discount_amount", "net_amount", "refund", "cogs"],
            money_cols=["gross_amount", "discount_amount", "net_amount", "refund", "cogs"], money_decimals=2, name=f"attributed_{sel}", alt="Attributed transactions", height=300)

with tab5:
    st.markdown("**A promotion changes more than sales.** Higher demand needs stock, stock needs purchases, and purchases tie up cash until customers pay. "
                "For each completed campaign this table shows the operational footprint:")
    camps = perf.set_index("campaign_id")
    inv = ds.t["inventory_daily"]
    rows = []
    attr = ds.lines[ds.lines["campaign_id"].notna()]
    for cid, r in camps.iterrows():
        a = attr[(attr["campaign_id"] == cid)]
        c0 = ds.t["campaigns"].set_index("campaign_id").loc[cid]
        w = inv[between(inv["balance_date"], c0["start_date"], c0["end_date"])]
        if isinstance(c0["target_category"], str):
            w = w[w["product_id"].map(ds.product_category) == c0["target_category"]]
        if isinstance(c0["store_id"], str):
            w = w[w["store_id"] == c0["store_id"]]
        rows.append({"campaign": r["campaign_name"], "attributed_units": a["quantity"].sum(), "attributed_cogs": a["cogs_amount"].sum(), "attributed_revenue": a["net_amount"].sum(),
                     "stock_out_product_days": int((w["quantity_on_hand"] == 0).sum()), "product_days_observed": len(w)})
    foot = pd.DataFrame(rows)
    foot["stock_out_share"] = [safe_div(a, b) for a, b in zip(foot["stock_out_product_days"], foot["product_days_observed"])]
    C.table(foot, money_cols=["attributed_cogs", "attributed_revenue"], pct_cols=["stock_out_share"], count_cols=["attributed_units", "stock_out_product_days", "product_days_observed"],
            labels={"attributed_cogs": "Stock cost of attributed sales", "stock_out_product_days": "Zero-stock product-days during campaign"}, name="campaign_operations", alt="Operational footprint of campaigns")
    st.page_link("app_pages/integrated_scenario.py", label="Open the connected scenario: spend → sales → stock → purchasing → cash", icon=":material/account_tree:")
