"""Reusable Streamlit building blocks: KPI cards, period picker, formatted tables with downloads."""
from __future__ import annotations

from typing import Dict, Iterable, Optional

import pandas as pd
import streamlit as st

from ..analytics.dataset import Dataset
from . import state
from .formatting import PRESETS, Currency, comparison_range, is_whole_months, money, preset_range


def kpi(label: str, value: str, delta: Optional[str] = None, *, help: Optional[str] = None, delta_color: str = "normal") -> None:
    st.metric(label, value, delta=delta, help=help, border=True, delta_color=delta_color)


def note(kind: str, text: str) -> None:
    """Small captions that keep period measures and point-in-time balances visibly separate."""
    icon = {"period": ":material/date_range:", "balance": ":material/event:", "info": ":material/info:", "warn": ":material/warning:",
            "sim": ":material/science:"}[kind]
    st.caption(f"{icon} {text}")


def definition(title: str, body: str) -> None:
    with st.expander(f"How is this calculated? {title}", icon=":material/help:"):
        st.markdown(body)


def period_picker(ds: Dataset, key: str, default: str = "Last 3 months", compare: bool = True) -> Dict:
    """Preset or custom date range (ending at the reference date) with an optional comparison window."""
    cur = state.currency()
    with st.container(horizontal=True, vertical_alignment="bottom"):
        preset = st.segmented_control("Period", PRESETS + ["Custom"], default=default, required=True, key=f"{key}_preset",
                                      help="Windows end at the reference date chosen in the sidebar.")
        mode = "None"
        if compare:
            mode = st.segmented_control("Compare with", ["Previous period", "Same period last year", "None"], default="Previous period",
                                        required=True, key=f"{key}_cmp")
    if preset == "Custom":
        s0, e0 = preset_range("Last 3 months", ds.ref_date, ds.start)
        picked = st.date_input("Custom range", value=(s0.date(), e0.date()), min_value=ds.start.date(), max_value=ds.end.date(), key=f"{key}_custom")
        if not isinstance(picked, (tuple, list)) or len(picked) != 2:
            st.info("Pick both a start and an end date.")
            st.stop()
        start, end = pd.Timestamp(picked[0]), pd.Timestamp(picked[1])
    else:
        start, end = preset_range(preset, ds.ref_date, ds.start)
    if start > end:
        st.error("The start date is after the end date.")
        st.stop()
    cmp_range = comparison_range(start, end, mode, ds.start) if mode != "None" else None
    if mode != "None" and cmp_range is None:
        st.caption(":material/info: No comparison shown: the comparison window would start before the first date in the data.")
    label = f"{start:%d %b %Y} to {end:%d %b %Y}"
    if not is_whole_months(start, end):
        st.caption(":material/info: This period is not a set of whole calendar months. Fixed costs (rent, wages) are booked on their payment "
                   "dates, so profit for part-months can look lumpy.")
    return {"start": start, "end": end, "cmp": cmp_range, "label": label, "mode": mode,
            "cmp_label": f"{cmp_range[0]:%d %b %Y} to {cmp_range[1]:%d %b %Y}" if cmp_range else None}


def table(df: pd.DataFrame, *, money_cols: Iterable[str] = (), pct_cols: Iterable[str] = (), count_cols: Iterable[str] = (),
          dec_cols: Iterable[str] = (), money_decimals: int = 0, labels: Optional[Dict[str, str]] = None, name: str = "table",
          height: Optional[int] = None, alt: Optional[str] = None, download: bool = True, hide_index: bool = True,
          key: Optional[str] = None, columns: Optional[Iterable[str]] = None) -> None:
    """Show a table with currency/percent formatting and a CSV download of exactly what is displayed."""
    cur: Currency = state.currency()
    labels = labels or {}
    cols = list(columns) if columns else list(df.columns)
    d = df[cols].copy()
    cfg = {}
    sym = cur.symbol.strip()
    for c in money_cols:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce") * cur.rate
            fmt = f"{sym} %,.{money_decimals}f"
            cfg[c] = st.column_config.NumberColumn(f"{labels.get(c, _nice(c))} ({cur.code})", format=fmt)
    for c in pct_cols:
        if c in d.columns:
            cfg[c] = st.column_config.NumberColumn(labels.get(c, _nice(c)), format="percent")
    for c in count_cols:
        if c in d.columns:
            cfg[c] = st.column_config.NumberColumn(labels.get(c, _nice(c)), format="%,d")
    for c in dec_cols:
        if c in d.columns:
            cfg[c] = st.column_config.NumberColumn(labels.get(c, _nice(c)), format="%.1f")
    for c in d.columns:
        if c not in cfg:
            cfg[c] = st.column_config.Column(labels.get(c, _nice(c)))
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            cfg[c] = st.column_config.DateColumn(labels.get(c, _nice(c)), format="DD MMM YYYY")
    st.dataframe(d, column_config=cfg, hide_index=hide_index, height=height or "auto", alt=alt or f"Table: {name}", key=key)
    if download and len(d):
        st.download_button("Download CSV", data=d.to_csv(index=False).encode("utf-8-sig"), file_name=f"retailiq_{name}.csv",
                           mime="text/csv", icon=":material/download:", key=f"dl_{key or name}")


def _nice(col: str) -> str:
    return str(col).replace("_", " ").strip().capitalize()


def fx_df(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """Copy of ``df`` with the given money columns converted to the display currency (for charts)."""
    cur = state.currency()
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce") * cur.rate
    return out


def mny(x: Optional[float], compact: bool = True) -> str:
    return money(x, state.currency(), compact=compact)


def empty_state(message: str) -> None:
    st.info(message, icon=":material/filter_alt_off:")


def multiselect_filter(label: str, options: Iterable, key: str, format_func=None, help: Optional[str] = None):
    """Multiselect that treats 'nothing selected' as 'everything' - and says so - rather than showing an empty page."""
    opts = list(options)
    sel = st.multiselect(label, opts, key=key, format_func=format_func or str, placeholder=f"All {label.lower()}", help=help)
    return sel or opts
