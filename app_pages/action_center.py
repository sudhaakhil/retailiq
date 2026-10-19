import pandas as pd
import streamlit as st

from retailiq.analytics import actions as AC
from retailiq.ui import cached, state
from retailiq.ui import components as C
from retailiq.ui.formatting import money

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)

st.write("What should we investigate or act on next? Each finding comes from a **fixed, readable rule** - the same data always gives the same findings, and no external AI service is used. "
         "Every card shows the trigger, the numbers, the date it refers to, a suggested next step and the limitations.")
C.note("balance", f"All findings are evaluated as at {ds.ref_date:%d %b %Y} (the reference date in the sidebar).")

defaults = AC.Settings()
with st.expander("Rule thresholds (editable)", icon=":material/tune:"):
    c1, c2, c3 = st.columns(3)
    reserve = c1.number_input(f"Minimum cash reserve ({cur.code})", min_value=0.0, value=float(round(st.session_state.get("cf_reserve", defaults.min_cash_reserve) * cur.rate)),
                              step=50000.0 * cur.rate, key=f"ac_reserve_{cur.code}") / cur.rate
    stress_drop = c2.slider("Stress test: sales drop (%)", 0, 60, 20, 5, key="ac_stress") / 100
    stress_delay = c3.slider("Stress test: credit customers pay later (days)", 0, 60, 15, 5, key="ac_delay")
    c4, c5, c6 = st.columns(3)
    slow_val = c4.number_input(f"Slow-moving stock alert at ({cur.code})", min_value=0.0, value=float(round(defaults.slow_stock_value_threshold * cur.rate)), step=10000.0 * cur.rate, key=f"ac_slow_{cur.code}") / cur.rate
    roas_min = c5.number_input("'Strong' ROAS from (×)", 0.5, 20.0, defaults.strong_roas, 0.5, key="ac_roas")
    margin_min = c6.slider("'Profitable' product margin from (%)", 0, 60, int(defaults.profitable_margin_min * 100), 5, key="ac_margin") / 100
    c7, c8, c9 = st.columns(3)
    supplier_min = c7.slider("Supplier on-time share below (%)", 50, 100, 80, 5, key="ac_supplier") / 100
    neg_count = c8.number_input("Repeated complaints: negative reviews from", 2, 30, defaults.negative_review_min_count, 1, key="ac_negcount")
    over = c9.slider("Opex over-budget alert (%)", 1, 50, 10, 1, key="ac_over") / 100
settings = AC.Settings(min_cash_reserve=float(reserve), stress_sales_drop=stress_drop, stress_collection_delay=int(stress_delay), slow_stock_value_threshold=float(slow_val), strong_roas=float(roas_min),
                       profitable_margin_min=margin_min, supplier_on_time_min=supplier_min, negative_review_min_count=int(neg_count), budget_overrun_pct=over)
findings = cached.action_findings(ds, key, settings)
fired = [f for f in findings if f.triggered]
quiet = [f for f in findings if not f.triggered]

with st.container(horizontal=True):
    C.kpi("Needs attention", f"{len(fired)}")
    C.kpi("High priority", f"{sum(f.severity == 'High' for f in fired)}", delta_color="off")
    C.kpi("Medium", f"{sum(f.severity == 'Medium' for f in fired)}", delta_color="off")
    C.kpi("Low", f"{sum(f.severity == 'Low' for f in fired)}", delta_color="off")
    C.kpi("Checked, nothing triggered", f"{len(quiet)}", delta_color="off")

areas = sorted({f.area for f in fired})
sev = st.segmented_control("Show", ["All", "High", "Medium", "Low"], default="All", required=True, key="ac_sev")
area_sel = st.multiselect("Area", areas, placeholder="All areas", key="ac_area")
shown = [f for f in fired if (sev == "All" or f.severity == sev) and (not area_sel or f.area in area_sel)]
if not shown:
    st.success("Nothing to show for this filter.", icon=":material/check_circle:")
ICON = {"High": ":material/error:", "Medium": ":material/warning:", "Low": ":material/info:"}
COLOR = {"High": "red", "Medium": "orange", "Low": "blue"}
for f in shown:
    with st.container(border=True):
        st.markdown(f":{COLOR[f.severity]}[{ICON[f.severity]} **{f.severity}**] · {f.area} · `{f.rule_id}`")
        st.markdown(f"### {f.title}")
        l, r = st.columns([3, 2])
        with l:
            st.markdown("**Triggering rule**  \n" + f.rule)
            st.markdown("**Supporting numbers**")
            for k, v in f.numbers.items():
                st.markdown(f"- {k}: **{v}**")
            st.caption(f":material/event: Reference: {f.reference}")
        with r:
            st.markdown("**Suggested investigation or action**  \n" + f.action)
            st.markdown("**Assumptions and limitations**  \n" + f.assumptions)
        if f.detail is not None and len(f.detail):
            with st.expander("Supporting records", icon=":material/table:"):
                money_cols = [c for c in f.detail.columns if any(t in c for t in ["value", "amount", "spend", "revenue", "contribution", "profit_at_risk", "closing_cash", "lowest_daily_cash", "monetary"])]
                C.table(f.detail, money_cols=money_cols, name=f"finding_{f.rule_id}", alt=f"Supporting records for {f.title}", key=f"ac_{f.rule_id}")
        page_map = {"cash_funding.py": "app_pages/cash_funding.py", "inventory.py": "app_pages/inventory.py", "finance.py": "app_pages/finance.py", "marketing.py": "app_pages/marketing.py",
                    "reviews.py": "app_pages/reviews.py", "capital.py": "app_pages/working_capital.py", "customers.py": "app_pages/customers.py", "data_definitions.py": "app_pages/data_definitions.py"}
        if f.page in page_map:
            st.page_link(page_map[f.page], label="Open the supporting page", icon=":material/arrow_forward:")

if quiet:
    with st.expander(f"Checked and not triggered ({len(quiet)})", icon=":material/check_circle:"):
        for f in quiet:
            st.markdown(f"**{f.title}** · {f.area} · `{f.rule_id}`")
            st.caption(f.rule)
            st.caption("; ".join(f"{k}: {v}" for k, v in f.numbers.items()))

export = pd.DataFrame([{"rule_id": f.rule_id, "triggered": f.triggered, "severity": f.severity, "area": f.area, "title": f.title, "rule": f.rule, "numbers": "; ".join(f"{k}: {v}" for k, v in f.numbers.items()),
                        "reference": f.reference, "action": f.action, "assumptions": f.assumptions} for f in findings])
st.download_button("Download all findings (CSV)", export.to_csv(index=False).encode("utf-8-sig"), "retailiq_action_center.csv", "text/csv", icon=":material/download:")
