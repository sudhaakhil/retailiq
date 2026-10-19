import pandas as pd
import streamlit as st

from retailiq.analytics import marketing as MK
from retailiq.analytics import scenarios as SC
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import money, pct

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("**Marketing spend ↑ → expected sales change → stock requirements change → purchasing costs change → cash and funding requirements change.** "
         "Change the assumptions and watch each link. The relationships are *assumptions you control*, not proven causal effects.")

camps = ds.t["campaigns"]
upcoming = camps[camps["end_date"] > ds.ref_date].sort_values("start_date")
perf = MK.campaign_performance(ds)
ch = MK.channel_summary(perf).set_index("channel") if not perf.empty else pd.DataFrame()

options = {r.campaign_id: f"{r.campaign_name} ({r.start_date:%d %b}-{r.end_date:%d %b %Y})" for r in upcoming.itertuples()}
options["custom"] = "Custom campaign (enter your own)"
sel = st.selectbox("Campaign to scale", list(options), format_func=options.get, key="sc_campaign")
if sel == "custom":
    c1, c2, c3, c4 = st.columns(4)
    name = "Custom campaign"
    budget = c1.number_input(f"Baseline budget ({cur.code})", min_value=1.0, value=float(round(100000 * cur.rate)), step=10000.0 * cur.rate, key=f"sc_cbudget_{cur.code}") / cur.rate
    start = pd.Timestamp(c2.date_input("Starts", value=(ds.ref_date + pd.Timedelta(days=21)).date(), min_value=(ds.ref_date + pd.Timedelta(days=1)).date(), max_value=(ds.ref_date + pd.Timedelta(days=80)).date(), key="sc_cstart"))
    days = c3.number_input("Duration (days)", 1, 60, 14, 1, key="sc_cdays")
    cat = c4.selectbox("Target category", ["All products"] + sorted(ds.t["products"]["category"].unique()), key="sc_ccat")
    camp = {"campaign_name": name, "planned_budget": budget, "start_date": start, "end_date": start + pd.Timedelta(days=int(days) - 1), "target_category": None if cat == "All products" else cat, "discount_pct": 0.1,
            "channel": "Social Media"}
else:
    row = upcoming.set_index("campaign_id").loc[sel]
    camp = {"campaign_name": row["campaign_name"], "planned_budget": float(row["planned_budget"]), "start_date": row["start_date"], "end_date": row["end_date"],
            "target_category": row["target_category"] if isinstance(row["target_category"], str) else None, "discount_pct": float(row["discount_pct"]), "channel": row["channel"]}
    st.caption(f"Planned: {money(camp['planned_budget'], cur, compact=True)} on {camp['channel']}, {camp['start_date']:%d %b} to {camp['end_date']:%d %b %Y}, "
               f"{camp['target_category'] or 'all categories'}, {camp['discount_pct']:.0%} discount.")
if camp["start_date"] > ds.ref_date + pd.Timedelta(days=91):
    st.warning("This campaign starts after the 13-week forecast window, so its cash effects fall outside what can be shown here.", icon=":material/warning:")

hist_roas = float(ch.loc[camp["channel"], "roas"]) if (not ch.empty and camp["channel"] in ch.index and pd.notna(ch.loc[camp["channel"], "roas"])) else 3.0
st.markdown("**Assumptions (all editable)**")
a1, a2, a3, a4, a5 = st.columns(5)
chg = a1.number_input("Change in marketing spend (%)", -90.0, 500.0, 50.0, 10.0, key="sc_chg", help="Applied to the campaign's baseline budget.") / 100
roas = a2.number_input("ROAS of the baseline campaign (×)", 0.0, 50.0, round(hist_roas, 2), 0.1, key="sc_roas", help=f"Default = this channel's historical ROAS ({hist_roas:.2f}×). Revenue per rupee of spend.")
elast = a3.slider("Response elasticity", 0.0, 1.2, 0.6, 0.05, key="sc_elast", help="1.0 = revenue grows in proportion to spend; lower = diminishing returns.")
base_share = a4.slider("Would have happened anyway (%)", 0, 80, 25, 5, key="sc_base") / 100
buf = a5.slider("Extra stock buffer (%)", 0, 50, 10, 5, key="sc_buf") / 100

planned = st.session_state.get("cf_planned")
recs = ()
if planned is not None:
    d = planned.dropna(subset=["planned_date", "amount"]).copy()
    d["planned_date"] = pd.to_datetime(d["planned_date"]).dt.strftime("%Y-%m-%d")
    d["category"] = d["category"].fillna("Other")
    d["description"] = d["description"].fillna("")
    d["activity"] = d["activity"].fillna("investing")
    recs = tuple(tuple(r) for r in d[["planned_date", "category", "description", "amount", "activity"]].itertuples(index=False, name=None))
reserve = float(st.session_state.get("cf_reserve", 750000.0))
reserve = st.number_input(f"Minimum cash reserve ({cur.code})", min_value=0.0, value=float(round(reserve * cur.rate)), step=50000.0 * cur.rate, key=f"sc_reserve_{cur.code}") / cur.rate
base = cached.cash_forecast(ds, key, 0.0, 0, 0, 40, reserve, 0.0, 0.0, recs)
res = SC.marketing_cash_scenario(ds, base, camp, chg, roas, elast, base_share, buf, reserve, 40)
if not res["valid"]:
    for pmsg in res["problems"]:
        st.error(pmsg)
    st.stop()

st.subheader("The chain, step by step", anchor=False)
chain = res["chain"]
cols = st.columns(len(chain))
fmt = [lambda v: M(v), lambda v: M(v), lambda v: M(v), lambda v: f"{v:,.0f} units", lambda v: f"{v:,.0f} units", lambda v: M(v)]
for col, (i, r) in zip(cols, chain.iterrows()):
    with col:
        with st.container(border=True):
            st.caption(r["step"])
            st.markdown(f"**{fmt[i](r['change'])}**" if i else f"**{M(r['scenario'])}**")
            st.caption(r["assumption"])
            if i == 0:
                st.caption(f"extra: {M(r['change'])}")
if res["extra_spend"] < 0:
    C.note("info", "A spend cut reduces revenue and the stock you need; negative values below mean less spending and less purchasing.")

st.subheader("What it does to cash and funding", anchor=False)
with st.container(horizontal=True):
    C.kpi("Lowest cash - base forecast", M(res["base_lowest_cash"]))
    C.kpi("Lowest cash - with scenario", M(res["scenario_lowest_cash"]), f"{money(res['scenario_lowest_cash'] - res['base_lowest_cash'], cur, compact=True)}", delta_color="normal",
          help=f"On {res['scenario_lowest_date']:%d %b %Y}")
    C.kpi("Closing cash at week 13", M(res["scenario_end_cash"]), f"{money(res['scenario_end_cash'] - res['base_end_cash'], cur, compact=True)}")
    C.kpi("Funding needed to hold the reserve", M(res["funding_need"]), help=f"Reserve {money(reserve, cur, compact=True)}")
    C.kpi("Profit effect inside the window", M(res["contribution_extra"]), help="Extra gross profit − extra marketing − variable selling costs, on sales inside the 13 weeks.", delta_color="off")
if res["breach"]:
    st.error(f"With this scenario cash would fall below the reserve (lowest {money(res['scenario_lowest_cash'], cur, compact=True)} on {res['scenario_lowest_date']:%d %b %Y}). Funding needed to hold the reserve: "
             f"{money(res['funding_need'], cur, compact=True)} - compare options on the Cash forecast & funding page.", icon=":material/warning:")
    st.page_link("app_pages/cash_funding.py", label="Compare funding options", icon=":material/payments:")
else:
    st.success("Cash stays above the reserve under this scenario.", icon=":material/check_circle:")
dd = res["daily"]
fig = CH.cash_path(C.fx_df(dd, ["base_closing_cash", "scenario_closing_cash"]), {"Base forecast": "base_closing_cash", "With scenario": "scenario_closing_cash"}, reserve * cur.rate, "Closing cash: base vs scenario", 380)
CH.show(fig, "Closing cash for the base forecast and the marketing scenario against the reserve", key="sc_path")
w = dd.assign(week=(range(len(dd)))).assign(week=lambda x: x["week"] // 7 + 1).groupby("week", as_index=False)[["collections", "purchases", "marketing_and_variable_costs", "overlay_flow"]].sum()
fig = CH.bars(C.fx_df(w, ["collections", "purchases", "marketing_and_variable_costs"]), "week", {"Extra collections": "collections", "Extra stock payments": "purchases", "Extra marketing & selling costs": "marketing_and_variable_costs"},
              "Weekly cash effect of the scenario", stacked=True, height=320)
CH.show(fig, "Weekly extra collections, stock payments and marketing costs caused by the scenario", key="sc_weekly")
st.subheader("Stock requirement behind the numbers", anchor=False)
stock = res["stock"]
C.table(stock.sort_values("purchase_cost", ascending=False), columns=["product_name", "extra_units_demand", "stock_needed_with_buffer", "surplus_stock_used", "units_to_buy", "unit_cost", "purchase_cost", "order_date", "payment_date",
                                                                       "capped_by_supplier_limit"],
        money_cols=["unit_cost", "purchase_cost"], dec_cols=["extra_units_demand", "stock_needed_with_buffer", "surplus_stock_used"], count_cols=["units_to_buy"],
        labels={"capped_by_supplier_limit": "Capped by supplier max"}, name="scenario_stock", alt="Stock needed for the scenario")
C.note("warn", res["caveat"])
C.definition("the scenario maths", "1. New spend S1 = S0 × (1 + change).  2. Extra attributed revenue = S0 × ROAS × ((S1/S0)^elasticity − 1).  3. Truly additional revenue = that × (1 − share that would have happened anyway).\n"
                                    "4. Units = revenue × recent product mix ÷ recent average price.  5. Stock to buy = units × (1 + buffer) − surplus stock above the reorder policy, rounded up to case packs and capped by supplier limits.\n"
                                    "6. Purchases are ordered one lead time before the campaign and paid after supplier terms; collections follow the usual payment mix; marketing and selling costs are paid at month end.")
