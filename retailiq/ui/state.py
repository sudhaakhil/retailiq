"""Shared Streamlit state: data loading, reference date and display currency."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

from ..analytics.dataset import Dataset
from ..config import DEFAULT_DB_PATH, GeneratorConfig
from ..db import database_status, initialise_database
from .formatting import ILLUSTRATIVE_RATES, Currency

DB_PATH = Path(DEFAULT_DB_PATH)


def db_signature(path: Path = DB_PATH) -> str:
    """Changes whenever the database file changes, so cached data refreshes automatically."""
    try:
        stat = path.stat()
        return f"{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        return "missing"


@st.cache_resource(ttl=6 * 3600, max_entries=2, show_spinner="Loading RetailIQ data ...")
def _load_dataset(db_path: str, signature: str) -> Dataset:  # signature is part of the cache key
    ds = Dataset.load(db_path)
    ds.warm()
    return ds


def setup_panel() -> None:
    st.warning("No RetailIQ database was found yet. Create the simulated dataset first.", icon=":material/database:")
    st.markdown(
        "Run this once in a terminal inside the project folder (with the virtual environment active):\n\n"
        "```powershell\npython -m retailiq.cli init-db\n```\n"
        "or press the button below. The data is **simulated**, reproducible (fixed seed) and never overwrites an existing database."
    )
    if st.button("Create simulated database now", type="primary", icon=":material/play_arrow:"):
        with st.spinner("Generating about 24 months of connected simulated data (about 10-20 seconds) ..."):
            try:
                msg = initialise_database(DB_PATH, GeneratorConfig(), reset=False, verbose=False)
                st.success(msg)
                st.cache_resource.clear()
                st.rerun()
            except Exception as exc:  # show the reason instead of a stack trace
                st.error(f"Could not create the database: {exc}")


def get_base_dataset() -> Dataset:
    if not DB_PATH.exists() or not database_status(DB_PATH):
        setup_panel()
        st.stop()
    return _load_dataset(str(DB_PATH), db_signature())


def get_dataset() -> Dataset:
    """Dataset with the sidebar's reference date applied (defaults to the latest date in the data)."""
    base = get_base_dataset()
    ref = st.session_state.get("ref_date")
    return base.with_ref_date(pd.Timestamp(ref)) if ref else base


def data_key(ds: Dataset) -> str:
    """Cache key that changes with the database file or the reference date."""
    return f"{db_signature()}|{ds.ref_date:%Y-%m-%d}"


def currency() -> Currency:
    code = st.session_state.get("currency", "INR")
    rate = 1.0 if code == "INR" else float(st.session_state.get(f"rate_{code}", ILLUSTRATIVE_RATES.get(code, 1.0)))
    if rate <= 0:
        rate = ILLUSTRATIVE_RATES.get(code, 1.0)
    return Currency(code, rate)


def render_sidebar() -> None:
    """Global controls shown on every page."""
    base = get_base_dataset()
    with st.sidebar:
        st.markdown("**Data scope**")
        st.date_input(
            "Reference date", value=base.end.date(), min_value=base.start.date(), max_value=base.end.date(), key="ref_date",
            help="The 'as at' date for balances (cash, stock, receivables) and the end of the default analysis period. "
                 "Defaults to the latest date in the data.")
        st.selectbox("Display currency", list(ILLUSTRATIVE_RATES), key="currency",
                     help="All data is stored in INR. Other currencies are a display conversion using the rate you enter below.")
        code = st.session_state.get("currency", "INR")
        if code != "INR":
            st.number_input(f"Conversion assumption: 1 INR = ? {code}", min_value=0.0001, value=ILLUSTRATIVE_RATES[code], format="%.4f",
                            key=f"rate_{code}", help="Illustrative placeholder, not a market rate. Edit it to your own assumption.")
            st.caption(f":material/warning: Amounts are converted at your assumed rate. Percentages and ratios are unaffected.")
        if st.button("Reload data", icon=":material/refresh:", help="Clears cached data and re-reads the database file.", width="stretch"):
            st.cache_resource.clear()
            st.cache_data.clear()
            st.rerun()
        meta = base.meta
        st.caption(f":material/science: **Simulated data** · seed {meta.get('seed')} · {base.start:%b %Y}–{base.end:%b %Y}")
