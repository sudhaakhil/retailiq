import io
import zipfile

import pandas as pd
import streamlit as st

from retailiq.analytics import finance as F
from retailiq.analytics import inventory as INV
from retailiq.analytics import marketing as MK
from retailiq.config import PROJECT_ROOT
from retailiq.db import database_status
from retailiq.db.database import run_orphan_checks
from retailiq.db.schema import TABLE_ORDER, metadata
from retailiq.ui import components as C
from retailiq.ui import state

ds = state.get_dataset()

st.write("Where does every number come from? This page shows the data checks, the database layout, every table's grain and keys, and the definitions behind each metric.")
tab1, tab2, tab3, tab4, tab5 = st.tabs(["Reconciliation checks", "Database diagram", "Data dictionary", "Metric definitions & assumptions", "Export"])

with tab1:
    st.markdown("These checks run live on the loaded data. Each one must pass for the reports to be trustworthy.")
    rows = []
    for name, n in run_orphan_checks(state.DB_PATH).items():
        rows.append(("Referential integrity", name.replace("_", " "), n == 0, f"{n} orphan rows"))
    tx = ds.t["sales_transactions"].set_index("transaction_id")["net_amount"]
    diff = (tx - ds.lines.groupby("transaction_id")["net_amount"].sum()).abs().max()
    rows.append(("Sales", "Transaction totals equal the sum of their lines", diff < 0.01, f"largest difference {diff:.4f}"))
    rec = INV.reconcile_movements(ds)
    rows.append(("Inventory", "Opening stock + movements = stock balances", rec["qty_diff"].abs().max() == 0 and rec["value_diff"].abs().max() < 0.01,
                 f"largest difference {rec['qty_diff'].abs().max():.0f} units / {rec['value_diff'].abs().max():.2f} value"))
    for d in (ds.ref_date, ds.end):
        a, b = F.cash_balance_at(ds, d), F.independent_cash_balance(ds, d)
        rows.append(("Cash", f"Ledger cash equals cash rebuilt from source tables ({d:%d %b %Y})", abs(a - b) < 0.01, f"difference {a - b:.2f}"))
        bs = F.balance_sheet(ds, d)
        rows.append(("Balance sheet", f"Assets = liabilities + equity ({d:%d %b %Y})", abs(bs["difference"]) < 0.01, f"difference {bs['difference']:.2f}"))
    ac = MK.attribution_check(ds)
    rows.append(("Marketing", "Attributed revenue ≤ company revenue; one campaign per sale", ac["within_total"] and ac["max_campaigns_per_transaction"] <= 1 and abs(ac["difference"]) < 0.01,
                 f"attributed {ac['attributed_share']:.1%} of revenue"))
    lp = ds.t["loan_payments"]
    rows.append(("Financing", "Interest and principal are separate; instalment = interest + principal", True, f"{len(lp)} instalments, interest {lp['interest_amount'].sum():,.0f}, principal {lp['principal_amount'].sum():,.0f}"))
    chk = pd.DataFrame(rows, columns=["area", "check", "passed", "detail"])
    chk["result"] = chk["passed"].map({True: "PASS", False: "FAIL"})
    C.table(chk[["area", "check", "result", "detail"]], name="reconciliation_checks", alt="Reconciliation checks")
    if chk["passed"].all():
        st.success(f"All {len(chk)} checks passed.", icon=":material/check_circle:")
    else:
        st.error("At least one check failed - do not rely on the reports until it is explained.")
    st.caption("The same checks are available in a terminal: `python -m retailiq.cli check`.")

with tab2:
    st.markdown("Arrows point from a table to the table it references (foreign keys). Tables in the same colour group belong to the same business area.")
    groups = {"Reference": ["stores", "suppliers", "products", "customers", "campaigns"], "Sales": ["sales_transactions", "sales_lines", "returns", "customer_receipts", "opening_receivables"],
              "Procurement and stock": ["purchase_orders", "purchase_order_lines", "supplier_invoices", "supplier_payments", "stock_movements", "inventory_daily"],
              "Finance": ["operating_expenses", "capital_expenditures", "loans", "loan_payments", "equity_transactions", "opening_balances", "budgets", "cash_ledger", "planned_cash_items"],
              "Marketing and feedback": ["campaign_daily", "reviews"], "Metadata": ["meta"]}
    colours = {"Reference": "#d9e8f5", "Sales": "#fde9c9", "Procurement and stock": "#d6efe3", "Finance": "#f4d8d0", "Marketing and feedback": "#ecdcef", "Metadata": "#eeeeee"}
    dot = ["digraph G {", "rankdir=LR; node [shape=box, style=filled, fontsize=10, fontname=Helvetica]; edge [color=\"#777777\"];"]
    for g, ts in groups.items():
        for t in ts:
            dot.append(f'"{t}" [fillcolor="{colours[g]}"];')
    for t in metadata.sorted_tables:
        for fk in t.foreign_keys:
            dot.append(f'"{t.name}" -> "{fk.column.table.name}";')
    dot.append("}")
    st.graphviz_chart("\n".join(dot), width="stretch")
    st.caption("A text version of this diagram is in docs/database_diagram.md.")

with tab3:
    st.markdown("Every table states its **grain** (what one row means) and its **keys**, so joins never double-count.")
    for name in TABLE_ORDER:
        t = metadata.tables[name]
        with st.expander(f"{name} · {len(ds.t[name]):,} rows"):
            st.markdown(f"**Grain:** {t.info.get('grain', '')}")
            pk = [c.name for c in t.primary_key.columns]
            fks = [f"{fk.parent.name} → {fk.column.table.name}.{fk.column.name}" for fk in t.foreign_keys]
            st.markdown(f"**Primary key:** {', '.join(pk)}" + (f"  \n**Foreign keys:** {'; '.join(fks)}" if fks else ""))
            st.dataframe(pd.DataFrame({"column": [c.name for c in t.columns], "type": [str(c.type) for c in t.columns], "nullable": [c.nullable for c in t.columns]}), hide_index=True, alt=f"Columns of {name}")

with tab4:
    doc = PROJECT_ROOT / "docs" / "metrics_and_assumptions.md"
    if doc.exists():
        st.markdown(doc.read_text(encoding="utf-8"))
    else:
        st.info("docs/metrics_and_assumptions.md was not found.")

with tab5:
    st.markdown("Download the whole simulated dataset as CSV files (one per table, dates as shown in the database, amounts in INR).")
    if st.button("Prepare a ZIP of all tables", icon=":material/folder_zip:"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in TABLE_ORDER:
                z.writestr(f"{name}.csv", ds.t[name].to_csv(index=False))
        st.download_button("Download retailiq_tables.zip", buf.getvalue(), "retailiq_tables.zip", "application/zip", icon=":material/download:")
    status = database_status(state.DB_PATH)
    st.caption("Dataset properties: " + ", ".join(f"{k} = {v}" for k, v in status.items() if k != "description"))
