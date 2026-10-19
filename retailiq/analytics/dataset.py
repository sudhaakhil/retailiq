"""In-memory analytical view of the RetailIQ database.

``Dataset`` loads every table once, normalises dates and builds a few derived
frames that all analytics modules share. It is READ-ONLY by convention: analytics
functions never modify it, which is what makes it safe to cache in Streamlit.

Key derived frames and their grain
----------------------------------
* ``lines``  - one row per sales line, enriched with transaction and product attributes.
* ``daily``  - one row per date x store x product: units, gross, discounts, net sales,
  COGS, and the returns recognised on that date. Used for fast period aggregation.
* ``dep``    - one row per asset x month: straight-line depreciation (derived, not stored).
"""
from __future__ import annotations

import datetime as dt
from functools import cached_property
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from ..config import DEFAULT_DB_PATH
from ..db.schema import DATE_COLUMNS
from .accounting import amortization_schedule, depreciation_schedule


def _normalise_dates(name: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in DATE_COLUMNS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def between(series: pd.Series, start, end) -> pd.Series:
    """Inclusive date-range mask."""
    return (series >= pd.Timestamp(start)) & (series <= pd.Timestamp(end))


class Dataset:
    """Container for all tables plus shared derived views."""

    def __init__(self, tables: Dict[str, pd.DataFrame], ref_date=None):
        self.t: Dict[str, pd.DataFrame] = {k: _normalise_dates(k, v) for k, v in tables.items()}
        self.meta: Dict[str, str] = dict(zip(self.t["meta"]["key"], self.t["meta"]["value"]))
        self.start = pd.Timestamp(self.meta["start_date"])
        self.end = pd.Timestamp(self.meta["end_date"])
        self.opening_date = self.start - pd.Timedelta(days=1)
        self.ref_date = pd.Timestamp(ref_date) if ref_date is not None else self.end
        self.currency = self.meta.get("base_currency", "INR")
        self.store_ids: List[str] = list(self.t["stores"]["store_id"])
        self.store_names: Dict[str, str] = dict(zip(self.t["stores"]["store_id"], self.t["stores"]["store_name"]))
        self.product_names: Dict[str, str] = dict(zip(self.t["products"]["product_id"], self.t["products"]["product_name"]))
        self.product_category: Dict[str, str] = dict(zip(self.t["products"]["product_id"], self.t["products"]["category"]))
        self.opening: Dict[str, float] = dict(zip(self.t["opening_balances"]["item"], self.t["opening_balances"]["amount"]))
        self._build_lines()

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_tables(cls, tables: Dict[str, pd.DataFrame], ref_date=None) -> "Dataset":
        return cls(tables, ref_date)

    @classmethod
    def load(cls, db_path: Path | str = DEFAULT_DB_PATH, ref_date=None) -> "Dataset":
        from ..db import load_all_tables

        return cls(load_all_tables(db_path), ref_date)

    def with_ref_date(self, ref_date) -> "Dataset":
        """Return a shallow copy that uses a different default reference date."""
        clone = object.__new__(Dataset)
        clone.__dict__.update(self.__dict__)
        clone.ref_date = min(max(pd.Timestamp(ref_date), self.start), self.end)
        return clone

    def _build_lines(self) -> None:
        t = self.t
        txn_cols = ["transaction_id", "transaction_date", "store_id", "customer_id", "channel", "payment_method", "campaign_id"]
        prod_cols = ["product_id", "product_name", "category", "supplier_id"]
        lines = t["sales_lines"].merge(t["sales_transactions"][txn_cols], on="transaction_id", how="left")
        lines = lines.merge(t["products"][prod_cols], on="product_id", how="left")
        lines["gross_amount"] = (lines["quantity"] * lines["unit_price"]).round(2)
        self.lines = lines

        ret = t["returns"].merge(t["products"][["product_id", "product_name", "category"]], on="product_id", how="left")
        ret = ret.merge(t["sales_transactions"][["transaction_id", "customer_id", "campaign_id", "transaction_date", "channel"]],
                        on="transaction_id", how="left")
        self.ret = ret

        sales = (lines.groupby(["transaction_date", "store_id", "product_id"], as_index=False)
                 .agg(units=("quantity", "sum"), gross=("gross_amount", "sum"), discount=("discount_amount", "sum"),
                      net=("net_amount", "sum"), cogs=("cogs_amount", "sum"), orders=("transaction_id", "nunique"))
                 .rename(columns={"transaction_date": "date"}))
        rets = (t["returns"].groupby(["return_date", "store_id", "product_id"], as_index=False)
                .agg(ret_units=("quantity", "sum"), refund=("refund_amount", "sum"), cogs_rev=("cogs_reversal", "sum"))
                .rename(columns={"return_date": "date"}))
        daily = sales.merge(rets, on=["date", "store_id", "product_id"], how="outer")
        for col in ["units", "gross", "discount", "net", "cogs", "orders", "ret_units", "refund", "cogs_rev"]:
            daily[col] = daily[col].fillna(0.0)
        daily["category"] = daily["product_id"].map(self.product_category)
        self.daily = daily.sort_values(["date", "store_id", "product_id"]).reset_index(drop=True)

    # ------------------------------------------------------------------ derived, cached frames
    @cached_property
    def dep(self) -> pd.DataFrame:
        return depreciation_schedule(self.t["capital_expenditures"], self.end.date())

    @cached_property
    def loan_schedules(self) -> Dict[str, pd.DataFrame]:
        out = {}
        for ln in self.t["loans"].itertuples(index=False):
            out[ln.loan_id] = amortization_schedule(float(ln.principal), float(ln.annual_rate), int(ln.term_months),
                                                    pd.Timestamp(ln.start_date).date(), int(ln.payment_day))
        return out

    @cached_property
    def calendar(self) -> pd.DatetimeIndex:
        return pd.date_range(self.start, self.end, freq="D")

    def txn_with_receipts(self) -> pd.DataFrame:
        """One row per transaction with its collection date (NaT when not yet collected by the end date)."""
        t = self.t
        rec = t["customer_receipts"].dropna(subset=["transaction_id"]).groupby("transaction_id", as_index=False).agg(
            receipt_date=("receipt_date", "max"), received=("amount", "sum"))
        tx = t["sales_transactions"].merge(rec, on="transaction_id", how="left")
        tx["received"] = tx["received"].fillna(0.0)
        return tx

    @cached_property
    def daily_balances(self) -> pd.DataFrame:
        """End-of-day balances for cash, inventory, receivables, payables and loans (index = date)."""
        t = self.t
        idx = pd.date_range(self.opening_date, self.end, freq="D")

        def cum(df: pd.DataFrame, date_col: str, val_col: str, start_value: float = 0.0) -> pd.Series:
            s = df.groupby(date_col)[val_col].sum() if not df.empty else pd.Series(dtype=float)
            return s.reindex(idx, fill_value=0.0).cumsum() + start_value

        led = t["cash_ledger"]
        cash = cum(led, "txn_date", "amount", self.opening["cash"])
        inv = (t["inventory_daily"].groupby("balance_date")["inventory_value"].sum()
               .reindex(idx).astype(float))
        inv.iloc[0] = self.opening["inventory"]
        inv = inv.ffill()
        tx = t["sales_transactions"]
        rec = t["customer_receipts"]
        # Receipts on the opening-receivable invoices reduce the opening AR; sales add new AR.
        ar = cum(tx, "transaction_date", "net_amount", self.opening["accounts_receivable"]) - cum(rec, "receipt_date", "amount")
        si = t["supplier_invoices"]
        sp = t["supplier_payments"]
        inv_all = si.groupby("invoice_date")["amount"].sum()
        ap_in = inv_all.reindex(idx.union(inv_all.index), fill_value=0.0).sort_index().cumsum().reindex(idx)
        ap_out = cum(sp, "payment_date", "amount")
        ap = ap_in - ap_out
        loans = t["loans"]
        draws = loans[loans["start_date"] >= self.start]
        loan = (self.opening["loan_principal"] + cum(draws, "start_date", "principal")
                - cum(t["loan_payments"], "payment_date", "principal_amount"))
        return pd.DataFrame({"cash": cash, "inventory": inv, "receivables": ar, "payables": ap, "loans": loan}).round(2)

    def warm(self) -> "Dataset":
        """Compute the lazily-built frames once so copies made by ``with_ref_date`` share them."""
        _ = self.dep, self.loan_schedules, self.calendar, self.daily_balances
        return self

    def product_label(self, pid: str) -> str:
        return f"{pid} · {self.product_names.get(pid, pid)}"

    def clamp_range(self, start, end) -> tuple[pd.Timestamp, pd.Timestamp]:
        s = max(pd.Timestamp(start), self.start)
        e = min(pd.Timestamp(end), self.end)
        return s, e
