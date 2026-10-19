import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from retailiq.analytics import forecasting as FC
from retailiq.analytics.dataset import between
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import money, pct

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("How many units of each product will each store sell in the next 7 and 30 days? Two methods are compared: a **seasonal-naive baseline** "
         "(repeat last week) and a **Ridge regression** on lagged demand, weekday, month and planned promotions.")
C.note("info", "Forecasts start the day after the reference date. Multi-day forecasts are recursive: day 2 uses the model's forecast for day 1, never the actual - so no future data leaks in.")

fc = cached.forecasts(ds, key, 30)
summ = FC.forecast_summary(ds, fc)
tab1, tab2, tab3 = st.tabs(["Forecast", "How good is it? (chronological validation)", "Method and safeguards"])

with tab1:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        store = st.selectbox("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], key="fc_store")
        prod = st.selectbox("Product", list(ds.product_names), format_func=ds.product_label, key="fc_prod")
        hz = st.segmented_control("Horizon", [7, 30], default=30, required=True, key="fc_hz", format_func=lambda h: f"{h} days")
    one = fc[(fc["store_id"] == store) & (fc["product_id"] == prod) & (fc["day_ahead"] <= hz)]
    sm = summ[(summ["store_id"] == store) & (summ["product_id"] == prod)].set_index("horizon_days")
    status = one["status"].iloc[0] if len(one) else ""
    if status != "model":
        st.warning(f"Status: {status}.", icon=":material/info:")
    with st.container(horizontal=True):
        for h in (7, 30):
            if h in sm.index:
                C.kpi(f"Next {h} days · model (units)", f"{sm.loc[h, 'model_units']:,.0f}", help=f"Baseline: {sm.loc[h, 'baseline_units']:,.0f} units. Estimated net sales {money(sm.loc[h, 'model_net_sales_est'], cur, compact=True)} at recent average prices.")
    hist = ds.daily[(ds.daily["store_id"] == store) & (ds.daily["product_id"] == prod) & between(ds.daily["date"], ds.ref_date - pd.Timedelta(days=89), ds.ref_date)]
    hist = hist.set_index("date")["units"].reindex(pd.date_range(ds.ref_date - pd.Timedelta(days=89), ds.ref_date), fill_value=0.0)
    fig = go.Figure()
    fig.add_scatter(x=hist.index, y=hist.values, mode="lines", name="Actual units", line=dict(color=CH.PALETTE[6], width=1.6))
    fig.add_scatter(x=one["date"], y=one["model_units"], mode="lines+markers", name="Ridge model", line=dict(color=CH.PALETTE[0], width=2.4))
    fig.add_scatter(x=one["date"], y=one["baseline_units"], mode="lines", name="Seasonal-naive baseline", line=dict(color=CH.PALETTE[1], width=2, dash="dot"))
    promo = one[one["promotion_planned"]]
    if len(promo):
        fig.add_vrect(x0=promo["date"].min(), x1=promo["date"].max(), fillcolor="rgba(0,114,178,0.10)", line_width=0, annotation_text="Planned promotion", annotation_position="top left")
    fig = CH._layout(fig, f"{ds.product_names[prod]} · {ds.store_names[store]}", 380, "Units per day", False)
    CH.show(fig, "Actual units over the last 90 days and the model and baseline forecasts", key="fc_chart")
    C.table(one, columns=["date", "day_ahead", "baseline_units", "model_units", "promotion_planned"], dec_cols=["baseline_units", "model_units"], labels={"day_ahead": "Day ahead"}, name="forecast_one", alt="Daily forecast for the selected series")
    st.subheader("All products", anchor=False)
    allv = summ[summ["horizon_days"] == hz].copy()
    allv["store"] = allv["store_id"].map(ds.store_names)
    C.table(allv.sort_values("model_net_sales_est", ascending=False), columns=["store", "product_name", "baseline_units", "model_units", "avg_net_price", "model_net_sales_est", "status"],
            money_cols=["avg_net_price", "model_net_sales_est"], dec_cols=["baseline_units", "model_units"], labels={"model_net_sales_est": "Estimated net sales (model)"}, name=f"forecast_all_{hz}d", alt="Forecast for all products and stores")
    st.caption("Forecasts are rounded in display only. Totals use the model forecast; where history is too short or sales stopped, the baseline or zero is used and the status says so.")

with tab2:
    st.markdown("The model is re-trained at six past **forecast origins** (every 28 days). Each time it sees only data up to the origin and is scored on the following days it has never seen. "
                "Scores are measured on the simulated dataset, so they show how the method behaves here - they are **not a promise about real-world accuracy**.")
    folds = st.slider("Number of validation folds", 3, 8, 6, key="fc_folds")
    v = cached.forecast_validation(ds, key, 30, int(folds))
    if not v["valid"]:
        st.warning(v["reason"])
    else:
        s = v["summary"]
        st.caption("Validation origins (train up to → test): " + "; ".join(f"{o:%d %b %y} → {a:%d %b}–{b:%d %b %y}" for o, a, b in v["folds"]))
        C.table(s, columns=["horizon_days", "series_x_folds", "baseline_MAE_units_per_day", "model_MAE_units_per_day", "baseline_WAPE", "model_WAPE", "baseline_total_demand_WAPE", "model_total_demand_WAPE",
                            "baseline_bias_pct", "model_bias_pct", "model_beats_baseline_on_MAE"],
                dec_cols=["baseline_MAE_units_per_day", "model_MAE_units_per_day"], pct_cols=["baseline_WAPE", "model_WAPE", "baseline_total_demand_WAPE", "model_total_demand_WAPE", "baseline_bias_pct", "model_bias_pct"],
                count_cols=["horizon_days", "series_x_folds"], labels={"baseline_MAE_units_per_day": "Baseline MAE (units/day)", "model_MAE_units_per_day": "Model MAE (units/day)", "baseline_WAPE": "Baseline WAPE (daily)",
                                                                       "model_WAPE": "Model WAPE (daily)", "baseline_total_demand_WAPE": "Baseline WAPE (horizon total)", "model_total_demand_WAPE": "Model WAPE (horizon total)",
                                                                       "baseline_bias_pct": "Baseline bias", "model_bias_pct": "Model bias", "model_beats_baseline_on_MAE": "Model beats baseline (MAE)"},
                name="forecast_validation", alt="Forecast validation metrics")
        r30 = s[s["horizon_days"] == 30].iloc[0] if (s["horizon_days"] == 30).any() else s.iloc[-1]
        st.info(f"**Reading this:** over {int(r30['horizon_days'])}-day horizons the model's average daily error is {r30['model_MAE_units_per_day']:.2f} units versus {r30['baseline_MAE_units_per_day']:.2f} for the baseline. "
                f"Daily WAPE is high ({pct(r30['model_WAPE'])}) because single-day demand is noisy; error on the *total* units over the horizon - what matters for purchasing - is {pct(r30['model_total_demand_WAPE'])}.", icon=":material/insights:")
        bs = v["by_series"].copy()
        bs["store"] = bs["store_id"].map(ds.store_names)
        C.table(bs.sort_values("model_WAPE"), columns=["store", "product_name", "actual", "baseline_WAPE", "model_WAPE"], dec_cols=["actual"], pct_cols=["baseline_WAPE", "model_WAPE"],
                labels={"actual": "Units sold in test windows"}, name="validation_by_series", alt="Validation error by product and store")
        C.definition("MAE and WAPE", "**MAE** (mean absolute error) = the average of |forecast − actual| per product-store-day, in units.  \n**WAPE** = Σ|forecast − actual| ÷ Σ actual. It stays defined when some days have zero sales "
                                     "(MAPE would divide by zero).  \n**Bias** = (Σ forecast − Σ actual) ÷ Σ actual over the horizon: negative means the forecast is too low on average.")

with tab3:
    st.markdown("""**Features** (for forecasting day *t* only information up to day *t − 1* is used):
lagged units (1, 7, 14, 28 days), 7- and 28-day rolling means of the *previous* days, weekday, month (sine/cosine) and a planned-promotion flag taken from the campaign calendar.

**Safeguards**
- Lags and rolling means are shifted so a day never sees its own value.
- Multi-day forecasts are recursive, so later days use earlier *forecasts*, never future actuals.
- Validation is chronological: the training period always ends before the test period begins. Random shuffling is never used.
- Series with fewer than 84 days of history use the baseline; series with no sales in the last 28 days forecast zero; forecasts are never negative.
- The promotion flag comes from the campaign plan, which is known in advance.

**Understanding the model.** Ridge regression is a linear model with a penalty that keeps coefficients small. Standardised coefficients show which inputs push the forecast up or down:""")
    s2 = st.selectbox("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], key="fc_coef_store")
    p2 = st.selectbox("Product", list(ds.product_names), format_func=ds.product_label, key="fc_coef_prod")
    co = FC.coefficients(ds, s2, p2)
    if co.empty:
        st.write("The model fell back to the baseline for this series, so there are no coefficients.")
    else:
        CH.show(CH.bars(co, "feature", {"Standardised coefficient": "coefficient"}, "Ridge coefficients (positive raises the forecast)", money_axis=False, horizontal=True, height=420), "Ridge regression coefficients", key="fc_coef")
