import pandas as pd
import streamlit as st

from retailiq.analytics import inventory as INV
from retailiq.analytics.accounting import safe_div
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import money, pct

ds = state.get_dataset()
cur = state.currency()
key = state.data_key(ds)
M = lambda x: money(x, cur, compact=True)  # noqa: E731

st.write("Which products need replenishment, how much cash is stuck in stock, and what would an order cost? "
         "Valuation uses the **weighted-average cost** method; replenishment rules are explained under each table.")
C.note("balance", f"Stock position at {ds.ref_date:%d %b %Y} (the reference date in the sidebar).")

Z = {"90%": 1.28, "95%": 1.65, "97.5%": 1.96, "99%": 2.33}
with st.expander("Planning assumptions", icon=":material/tune:", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    service = c1.selectbox("Service level (safety stock)", list(Z), index=1, key="inv_service",
                           help="Chance of not running out during a replenishment cycle. Higher = more safety stock = more cash tied up.")
    cover = c2.number_input("Days of cover to order up to (after lead time)", 0, 180, 30, 5, key="inv_cover", help="Order-up-to level = demand × (lead time + cover) + safety stock.")
    method = c3.selectbox("Demand estimate", ["trailing", "seasonal"], index=1, key="inv_method",
                          format_func=lambda m: {"trailing": "Recent average (last N days)", "seasonal": "Same period last year × growth"}[m],
                          help="Seasonal needs at least ~13 months of history; otherwise the recent average is used automatically.")
    window = c4.number_input("Recent-average window (days)", 14, 365, 90, 7, key="inv_window")
    c5, c6, c7 = st.columns(3)
    fast = c5.number_input("Fast-moving if days of stock ≤", 5, 90, 30, 5, key="inv_fast")
    slow = c6.number_input("Slow-moving if days of stock >", 30, 365, 120, 10, key="inv_slow")
    over = c7.number_input("Overstock if cover incl. on-order >", 30, 365, 120, 10, key="inv_over")

plan = cached.replenishment(ds, key, int(window), Z[service], int(cover), method, int(slow), int(fast), int(over))
if plan.empty:
    C.empty_state("No stock data at this reference date.")
    st.stop()
with st.container(horizontal=True, vertical_alignment="bottom"):
    stores = st.multiselect("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], placeholder="All stores", key="inv_store")
    cats = st.multiselect("Category", sorted(plan["category"].unique()), placeholder="All categories", key="inv_cat")
    status = st.multiselect("Status", sorted(plan["status"].unique()), placeholder="Any status", key="inv_status")
view = plan[plan["store_id"].isin(stores or ds.store_ids) & plan["category"].isin(cats or plan["category"].unique()) & plan["status"].isin(status or plan["status"].unique())]
if view.empty:
    C.empty_state("No stock lines match these filters.")
    st.stop()
sm = INV.plan_summary(view)
if "seasonal" in method and (view["demand_basis"] == "trailing average").all():
    C.note("warn", "Not enough history for a seasonal estimate at this date, so the recent average was used.")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Stock health", "Reorder plan & budget", "Movement history", "Suppliers & lead times", "Turnover"])

# ============================================================= stock health
with tab1:
    with st.container(horizontal=True):
        C.kpi("Inventory value", M(sm["inventory_value"]), help="Quantity on hand × weighted-average unit cost.")
        C.kpi("Slow-moving / no-sales stock", M(sm["slow_value"]), help=f"{pct(sm['slow_share'])} of inventory value. Cash that is not turning over.", delta_color="off")
        C.kpi("Low-stock lines", f"{sm['low_stock_lines']}", help="Stock on hand + on order is at or below the reorder point.")
        C.kpi("Out-of-stock lines", f"{sm['out_of_stock_lines']}")
        C.kpi("Overstock lines", f"{sm['overstock_lines']}")
        C.kpi("Suggested purchases", M(sm["suggested_cost"]), help="Cost of all suggested reorder quantities (see next tab).")
    chart = view.assign(label=view["store_id"] + " · " + view["product_name"]).sort_values("days_of_stock", ascending=False).head(25)
    chart = chart[chart["days_of_stock"].notna()]
    fig = CH.bars(chart, "label", {"Days of stock": "days_of_stock"}, "Days of stock on hand (top 25 longest)", money_axis=False, horizontal=True, height=520, y_title="Days")
    CH.show(fig, "Days of stock on hand by store and product", key="inv_days_chart")
    C.table(view.sort_values("days_of_stock"), columns=["store_id", "product_name", "category", "on_hand", "on_order", "inventory_value", "daily_demand", "days_of_stock", "turnover_annualised", "status", "movement"],
            money_cols=["inventory_value"], count_cols=["on_hand", "on_order"], dec_cols=["daily_demand", "days_of_stock", "turnover_annualised"],
            labels={"daily_demand": "Units/day", "turnover_annualised": "Turns/year"}, name="stock_health", alt="Stock health by store and product")
    C.definition("stock health", "- **Days of stock** = quantity on hand ÷ expected daily demand.\n- **Turns per year** = annualised COGS ÷ average inventory value over the window.\n"
                                 "- **Status**: *Out of stock* (nothing on hand); *Low stock - reorder* (on hand + on order ≤ reorder point); *Overstock* (cover incl. on-order above your limit); otherwise *OK*.\n"
                                 "- **Movement**: *Fast* ≤ your fast limit; *Slow* > your slow limit; *No sales* when there was no demand in the window.")

# ============================================================= reorder plan & budget
with tab2:
    orders = view[view["suggested_qty"] > 0].copy()
    st.markdown("**Suggested orders** - quantity = order-up-to level − (on hand + on order), rounded **up** to the supplier case pack, never negative, and capped at the supplier's maximum order.")
    if orders.empty:
        st.success("No replenishment is needed under these assumptions.")
    else:
        C.table(orders.sort_values("est_purchase_cost", ascending=False),
                columns=["store_id", "product_name", "supplier_id", "on_hand", "on_order", "daily_demand", "lead_time_days", "safety_stock", "reorder_point", "order_up_to", "suggested_qty",
                         "case_pack", "unit_cost", "est_purchase_cost", "cover_after_order_days", "capped_by_supplier_limit"],
                money_cols=["unit_cost", "est_purchase_cost"], count_cols=["on_hand", "on_order", "suggested_qty", "case_pack"],
                dec_cols=["daily_demand", "lead_time_days", "safety_stock", "reorder_point", "order_up_to", "cover_after_order_days"],
                labels={"daily_demand": "Units/day", "lead_time_days": "Lead time (days)", "cover_after_order_days": "Cover after order (days)", "capped_by_supplier_limit": "Capped by supplier max"},
                name="reorder_plan", alt="Suggested reorder quantities", money_decimals=0)
        C.note("info", "Safety stock = z × √(lead time × demand variance + demand² × lead-time variance), using each supplier's observed lead time and its variability. "
                       "Reorder point = demand × lead time + safety stock. If a supplier cap binds, the line is flagged - the app never assumes unlimited supply.")
        st.subheader("How much cash does an order need?", anchor=False)
        sup = ds.t["suppliers"]
        need = orders.groupby("supplier_id", as_index=False).agg(order_cost=("est_purchase_cost", "sum"), lines=("product_id", "count")).merge(
            sup[["supplier_id", "supplier_name", "payment_terms_days"]], on="supplier_id")
        need["cash_due_on"] = [ds.ref_date + pd.Timedelta(days=int(lt) + int(t)) for lt, t in zip(
            [view[view["supplier_id"] == s]["lead_time_days"].mean() for s in need["supplier_id"]], need["payment_terms_days"])]
        c1, c2 = st.columns([2, 3])
        with c1:
            C.kpi("Cash needed if everything is ordered", M(need["order_cost"].sum()), help="Total purchase cost; cash leaves when each supplier's invoice falls due.")
            st.caption("The cash is paid after the goods arrive plus the supplier's payment terms, so the order is partly financed by the supplier.")
        with c2:
            C.table(need, columns=["supplier_name", "lines", "order_cost", "payment_terms_days", "cash_due_on"], money_cols=["order_cost"], count_cols=["lines", "payment_terms_days"],
                    labels={"cash_due_on": "Approx. cash due"}, name="order_cash_by_supplier", alt="Cash needed per supplier")
        st.subheader("Limited purchasing budget", anchor=False)
        use_budget = st.toggle("Apply a purchasing budget", value=True, key="inv_use_budget")
        budget = None
        if use_budget:
            default_budget = float(round(orders["est_purchase_cost"].sum() * 0.4 / 5000) * 5000) or 50000.0
            budget = st.number_input(f"Budget for this purchasing round ({cur.code})", min_value=0.0, value=float(round(default_budget * cur.rate)), step=5000.0 * cur.rate, key=f"inv_budget_{cur.code}") / cur.rate
        alloc = INV.prioritise_orders(orders, budget)
        spent = float(alloc["funded_cost"].sum())
        ess_cost = float(alloc["essential_cost"].sum())
        with st.container(horizontal=True):
            C.kpi("Spent", M(spent), help="Cost of units funded.")
            C.kpi("Essential units cost", M(ess_cost), help="Cost of units needed just to restore each line to its reorder point.")
            C.kpi("Not funded", M(float(alloc["est_purchase_cost"].sum() - spent)), help="Suggested purchases the budget could not cover.", delta_color="off")
        if budget is not None and budget < ess_cost:
            st.warning(f"The budget does not even cover the essential units ({money(ess_cost, cur, compact=True)}). Some lines will stay below their reorder point.", icon=":material/warning:")
        C.table(alloc, columns=["decision", "store_id", "product_name", "essential_qty", "suggested_qty", "funded_qty", "funded_cost", "profit_at_risk", "gmroi", "cover_after_funded_days", "cumulative_cost"],
                money_cols=["funded_cost", "profit_at_risk", "cumulative_cost"], count_cols=["essential_qty", "suggested_qty", "funded_qty"], dec_cols=["gmroi", "cover_after_funded_days"],
                labels={"gmroi": "GMROI", "profit_at_risk": "Profit at risk", "cover_after_funded_days": "Cover after funding (days)"}, name="budget_allocation", alt="Budget allocation by order line")
        with st.expander("How the budget is allocated - and the trade-offs", icon=":material/balance:"):
            st.markdown(
                "**Phase 1 - essential units.** For each line, *essential units* bring stock back to the reorder point. Lines are ranked by "
                "`profit at risk per rupee` = daily demand × unit margin × days not covered by stock before the next delivery ÷ essential cost. Higher means more lost profit per rupee left unspent.\n\n"
                "**Phase 2 - extra cover.** Remaining money buys extra cover units, ranked by GMROI (annual gross profit per rupee of average inventory) - where a rupee of stock earns the most.\n\n"
                "Units are bought in whole case packs; the last funded line can be partly funded.\n\n"
                "**Trade-offs.** Spending more *availability* (higher service level, more cover) protects sales but ties up cash; favouring *margin* (GMROI) puts cash in the best-earning products but "
                "can leave slower, lower-margin lines short; holding *less cash in stock* frees money but raises stock-out risk. The ranking is a transparent rule of thumb, not an optimiser.")

# ============================================================= movement history
with tab3:
    p = C.period_picker(ds, "inv_mv", default="Last 3 months", compare=False)
    with st.container(horizontal=True):
        mv_prod = st.multiselect("Product", list(ds.product_names), format_func=ds.product_label, placeholder="All products", key="inv_mv_prod")
        mv_types = st.multiselect("Movement type", ["opening", "receipt", "sale", "return"], placeholder="All types", key="inv_mv_type")
    mv = INV.stock_movement_history(ds, p["start"], p["end"], stores or None, mv_prod or None, mv_types or None)
    if mv.empty:
        C.empty_state("No stock movements match these filters.")
    else:
        summ = mv.groupby("movement_type", as_index=False).agg(movements=("movement_id", "count"), units=("quantity", "sum"), value=("value", "sum"))
        C.table(summ, money_cols=["value"], count_cols=["movements", "units"], name="movement_summary", alt="Stock movement summary", download=False)
        C.table(mv.sort_values("movement_date", ascending=False).head(1000), columns=["movement_date", "store_id", "product_name", "movement_type", "quantity", "unit_cost", "value", "reference_id"],
                money_cols=["unit_cost", "value"], count_cols=["quantity"], money_decimals=2, name="stock_movements", alt="Stock movements", download=False)
        st.download_button("Download all matching movements (CSV)", mv.to_csv(index=False).encode("utf-8-sig"), "retailiq_stock_movements.csv", "text/csv", icon=":material/download:")
    trend = INV.inventory_value_trend(ds)
    trend = trend.groupby("balance_date", as_index=False)["inventory_value"].sum()
    CH.show(CH.lines(C.fx_df(trend, ["inventory_value"]), "balance_date", {"Inventory value": "inventory_value"}, "Inventory value over time (all stores)"), "Inventory value over time", key="inv_trend")
    rec = INV.reconcile_movements(ds)
    ok = rec["qty_diff"].abs().max() == 0 and rec["value_diff"].abs().max() < 0.01
    if ok:
        st.success(f"Reconciliation: opening stock + all movements = stock balance for every store and product (largest difference {rec['qty_diff'].abs().max():.0f} units, {rec['value_diff'].abs().max():.2f} value).",
                   icon=":material/check_circle:")
    else:
        st.error("Stock movements do not reconcile with balances.")

# ============================================================= suppliers
with tab4:
    lt = INV.supplier_lead_times(ds)
    C.table(lt, columns=["supplier_name", "orders", "quoted_lead_days", "mean_lead_days", "std_lead_days", "on_time_share", "avg_days_late_when_late", "payment_terms_days"],
            pct_cols=["on_time_share"], count_cols=["orders", "payment_terms_days"], dec_cols=["mean_lead_days", "std_lead_days", "avg_days_late_when_late"],
            labels={"quoted_lead_days": "Quoted lead (days)", "mean_lead_days": "Observed lead (days)", "std_lead_days": "Lead-time std dev", "on_time_share": "Received on time",
                    "avg_days_late_when_late": "Avg days late (when late)"}, name="supplier_lead_times", alt="Supplier lead times")
    fig = CH.bars(lt.assign(quoted=lt["quoted_lead_days"].astype(float)), "supplier_name", {"Quoted lead time": "quoted", "Observed mean lead time": "mean_lead_days"},
                  "Quoted versus observed lead time (last 12 months)", money_axis=False, y_title="Days")
    CH.show(fig, "Quoted versus observed supplier lead time", key="inv_lt")
    C.note("info", "The reorder logic uses OBSERVED lead times and their variability, so unreliable suppliers automatically receive larger safety stock (and tie up more cash).")

# ============================================================= turnover
with tab5:
    t = view.groupby(["product_id", "product_name"], as_index=False).agg(inventory_value=("inventory_value", "sum"), cogs=("cogs_window", "sum"), avg_inv=("avg_inventory_window", "sum"),
                                                                       gp=("units_sold_window", "sum"))
    t["turnover"] = [safe_div(c * 365.0 / max(int(window), 1), a) for c, a in zip(t["cogs"], t["avg_inv"])]
    t["days_to_sell"] = [safe_div(365.0, x) for x in t["turnover"]]
    C.table(t.sort_values("turnover", ascending=False), columns=["product_name", "inventory_value", "turnover", "days_to_sell"], money_cols=["inventory_value"], dec_cols=["turnover", "days_to_sell"],
            labels={"turnover": "Turns per year", "days_to_sell": "Days to sell through"}, name="inventory_turnover", alt="Inventory turnover by product")
    C.definition("inventory turnover", "Inventory turnover = annualised cost of goods sold ÷ average inventory value over the recent-average window. "
                                       "Higher is better until it causes stock-outs; very low values flag cash tied up in slow stock.")
