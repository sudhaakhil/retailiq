import pandas as pd
import streamlit as st

from retailiq.analytics import capital as K
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui import state
from retailiq.ui.formatting import delta_text, money, pct

ds = state.get_dataset()
cur = state.currency()
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("How much money is tied up in running the business, and how much capital do we need to operate and grow? "
         "**Working capital = inventory + receivables − payables.** It is cash that is stuck in stock and unpaid sales, partly funded by suppliers.")

with st.container(horizontal=True, vertical_alignment="bottom"):
    date = st.date_input("Balances as at", value=ds.ref_date.date(), min_value=ds.start.date(), max_value=ds.end.date(), key="wc_date")
    window = st.number_input("Days of sales/costs used for the days metrics", min_value=7, max_value=365, value=90, step=1, key="wc_window",
                             help="Balances are divided by the average daily flow over this many days before the date.")
    reserve_days = st.number_input("Minimum cash cover (days of operating outflows)", min_value=0, max_value=90, value=14, step=1, key="wc_reserve_days",
                                   help="Used for the estimated minimum operating cash requirement.")
date = pd.Timestamp(date)
snap = K.working_capital_snapshot(ds, date, int(window), int(reserve_days))
prev_date = date - pd.DateOffset(months=3)
snap0 = K.working_capital_snapshot(ds, prev_date, int(window), int(reserve_days)) if prev_date >= ds.start else None
for n in snap["notes"]:
    C.note("warn", n)

C.note("balance", f"Balances at {date:%d %b %Y}" + (f"; change vs {prev_date:%d %b %Y}" if snap0 else ""))
g = lambda k: snap0[k] if snap0 else None  # noqa: E731
with st.container(horizontal=True):
    C.kpi("Cash available", M(snap["cash"]), delta_text(snap["cash"], g("cash")))
    C.kpi("Inventory", M(snap["inventory"]), delta_text(snap["inventory"], g("inventory")), delta_color="off")
    C.kpi("Receivables", M(snap["receivables"]), delta_text(snap["receivables"], g("receivables")), delta_color="off")
    C.kpi("Payables", M(snap["payables"]), delta_text(snap["payables"], g("payables")), delta_color="off")
    C.kpi("Working-capital requirement", M(snap["operating_working_capital"]), delta_text(snap["operating_working_capital"], g("operating_working_capital")), delta_color="off",
          help="Inventory + receivables − payables.")
C.note("period", f"Days metrics use flows over {snap['window_start']:%d %b %Y} to {snap['window_end']:%d %b %Y} ({snap['window_days']} days).")
with st.container(horizontal=True):
    d = lambda v: f"{v:.1f} days" if v is not None else "n/a"  # noqa: E731
    C.kpi("Inventory days", d(snap["inventory_days"]), help="Inventory ÷ (COGS per day)")
    C.kpi("Receivable days", d(snap["receivable_days"]), help="Receivables ÷ (net sales per day). Low because most customers pay at the till; business customers have 30-day terms.")
    C.kpi("Payable days", d(snap["payable_days"]), help="Payables ÷ (COGS per day)")
    C.kpi("Cash conversion cycle", d(snap["cash_conversion_cycle"]), help="Inventory days + receivable days − payable days: how long cash is tied up between paying suppliers and collecting from customers.")
    C.kpi("Minimum operating cash", M(snap["min_operating_cash"]), help=f"{int(reserve_days)} days × average daily operating cash outflow ({money(snap['daily_operating_outflow'], cur, compact=True)}/day).")
headroom = snap["cash"] - snap["min_operating_cash"]
st.write(f"Cash above the estimated minimum: **{money(headroom, cur, compact=True)}**" + (" (below the minimum - see Cash forecast & funding)" if headroom < 0 else ""))
C.definition("these metrics", "- **Operating working capital** = inventory + accounts receivable − accounts payable.\n"
                              "- **Inventory days** = inventory ÷ COGS per day;  **Receivable days** = receivables ÷ net sales per day;  **Payable days** = payables ÷ COGS per day.\n"
                              "- **Cash conversion cycle** = inventory days + receivable days − payable days.\n"
                              "- **Minimum operating cash** = average daily operating cash outflow × cover days (an estimate; set your own reserve in the scenario below).\n"
                              "- Unsuitable windows: if fewer than 28 days of history exist, or sales/COGS are zero, the days metrics are shown as n/a rather than guessed.")

tab_trend, tab_aging, tab_scn = st.tabs(["Trend", "Ageing of receivables and payables", "Scenario tool"])
with tab_trend:
    tr = K.working_capital_trend(ds)
    tr = tr[tr["date"] <= date]
    fig = CH.lines(C.fx_df(tr, ["inventory", "receivables", "payables", "operating_working_capital"]), "date",
                   {"Inventory": "inventory", "Receivables": "receivables", "Payables": "payables", "Working-capital requirement": "operating_working_capital"},
                   "Month-end working-capital components", dash={"Working-capital requirement": "dash"})
    CH.show(fig, "Month-end inventory, receivables, payables and the working capital requirement", key="wc_trend")
    rows = []
    for me in pd.date_range(ds.start, date, freq="ME"):
        s = K.working_capital_snapshot(ds, me, int(window), int(reserve_days))
        rows.append({"date": me, "Inventory days": s["inventory_days"], "Receivable days": s["receivable_days"], "Payable days": s["payable_days"], "Cash conversion cycle": s["cash_conversion_cycle"]})
    ccc = pd.DataFrame(rows).dropna()
    if len(ccc):
        fig = CH.lines(ccc, "date", {c: c for c in ["Inventory days", "Receivable days", "Payable days", "Cash conversion cycle"]}, "Days metrics at each month end", money_axis=False,
                       y_title="Days", dash={"Cash conversion cycle": "dash"})
        CH.show(fig, "Inventory, receivable and payable days and the cash conversion cycle by month end", key="wc_days")
with tab_aging:
    a, b = st.columns(2)
    with a:
        st.markdown("**Customer invoices outstanding**")
        C.table(K.receivable_aging(ds, date), money_cols=["amount"], name="receivable_aging", alt="Receivable ageing")
        C.note("info", "Card/UPI settlement in transit is money the payment processor owes you for 1-2 days; it is not a collection risk.")
    with b:
        st.markdown("**Supplier invoices outstanding**")
        C.table(K.payable_aging(ds, date), money_cols=["amount"], name="payable_aging", alt="Payable ageing")

with tab_scn:
    st.markdown("Change the assumptions to see how much cash the business would need. Everything starts at today's actual values.")
    if snap["daily_net_sales"] is None or snap["daily_net_sales"] <= 0:
        st.warning("There are no sales in the window before this date, so a growth scenario cannot be scaled. Choose a later date or a longer window.")
        st.stop()
    base_gm = snap["gross_margin"] or 0.0
    with st.form("wc_form"):
        c1, c2, c3 = st.columns(3)
        growth = c1.number_input("Sales growth (%)", min_value=-90.0, max_value=500.0, value=20.0, step=5.0, help="Change in average daily sales versus today.") / 100
        gm = c2.number_input("Gross margin (%)", min_value=-100.0, max_value=99.0, value=round(base_gm * 100, 1), step=0.5, help=f"Today: {pct(base_gm)}") / 100
        reserve = c3.number_input(f"Minimum cash reserve ({cur.code})", min_value=0.0, value=round(snap["min_operating_cash"] * cur.rate, -3), step=50000.0 * cur.rate,
                                  key=f"wc_reserve_{cur.code}", help="Cash you want to keep untouched.") / cur.rate
        c4, c5, c6 = st.columns(3)
        inv_days = c4.number_input("Inventory holding period (days)", min_value=0.0, max_value=365.0, value=round(snap["inventory_days"] or 0, 1), step=1.0)
        col_days = c5.number_input("Customer collection period (days)", min_value=0.0, max_value=180.0, value=round(snap["receivable_days"] or 0, 1), step=0.5)
        pay_days = c6.number_input("Supplier payment period (days)", min_value=0.0, max_value=180.0, value=round(snap["payable_days"] or 0, 1), step=1.0)
        st.form_submit_button("Calculate", type="primary", icon=":material/calculate:")
    res = K.wc_scenario(snap, growth, gm, inv_days, col_days, pay_days, reserve)
    if not res["valid"]:
        for pmsg in res["problems"]:
            st.error(pmsg)
        st.stop()
    with st.container(horizontal=True):
        C.kpi("Extra cash tied up in working capital", M(res["delta_owc"]), help="Scenario working-capital requirement minus today's.", delta_color="off")
        C.kpi("Cash after funding that", M(res["cash_after_working_capital"]), help="Today's cash minus the extra working capital.")
        C.kpi("Reserve to keep", M(res["min_cash_reserve"]))
        C.kpi("Surplus (+) / shortfall (−)", M(res["cash_after_working_capital"] - res["min_cash_reserve"]))
    if res["shortfall"] > 0:
        st.warning(f"Under these assumptions you would need about **{money(res['shortfall'], cur, compact=True)}** of extra funding to keep the reserve. See Cash forecast & funding for options.", icon=":material/warning:")
    else:
        st.success("Under these assumptions cash stays above the reserve after funding the extra working capital.", icon=":material/check_circle:")
    tbl = pd.DataFrame({"item": ["Inventory", "Receivables", "Payables", "Working-capital requirement"],
                        "today (model)": [res["baseline"][k] for k in ["inventory", "receivables", "payables", "owc"]],
                        "scenario": [res["scenario"][k] for k in ["inventory", "receivables", "payables", "owc"]]})
    tbl["change"] = tbl["scenario"] - tbl["today (model)"]
    C.table(tbl, money_cols=["today (model)", "scenario", "change"], name="wc_scenario", alt="Working capital scenario", download=True)
    st.subheader("What drives the answer", anchor=False)
    sens = res["sensitivity"].copy()
    fig = CH.bars(C.fx_df(sens.sort_values("effect_on_cash_needed"), ["effect_on_cash_needed"]), "driver", {"Effect on cash needed": "effect_on_cash_needed"},
                  "Effect of each assumption on its own (positive = needs more cash)", horizontal=True, height=300)
    CH.show(fig, "Effect of each scenario assumption on cash needed when changed on its own", key="wc_sens")
    C.table(sens, money_cols=["effect_on_cash_needed"], labels={"your_setting": "Your setting", "why_it_matters": "Why it matters"}, name="wc_sensitivity", alt="Sensitivity of cash needed to each assumption")
    C.note("info", "Each row changes ONE assumption from today's value. Effects interact, so they do not add up exactly to the total. The model captures the timing of cash in stock and "
                   "receivables only - not extra profit, taxes, new stores or equipment.")
