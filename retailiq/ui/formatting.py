"""Number, currency and period formatting helpers (no Streamlit import, so they are unit-testable)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

from ..config import BASE_CURRENCY, CURRENCY_SYMBOLS

# Illustrative conversion assumptions per 1 INR. They are placeholders the user must review and edit - not market rates.
ILLUSTRATIVE_RATES = {"INR": 1.0, "USD": 0.012, "EUR": 0.011, "GBP": 0.0095, "AED": 0.044}


@dataclass(frozen=True)
class Currency:
    """Display currency. All stored amounts are in INR; ``rate`` converts 1 INR into the display currency."""
    code: str = BASE_CURRENCY
    rate: float = 1.0

    @property
    def symbol(self) -> str:
        return CURRENCY_SYMBOLS.get(self.code, self.code + " ")

    def convert(self, x):
        return x * self.rate


def money(x: Optional[float], cur: Currency, compact: bool = False, decimals: int = 0) -> str:
    """Format an INR amount in the display currency. ``None``/NaN -> 'n/a'. Indian lakh/crore units for INR."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    v = cur.convert(float(x))
    sign = "-" if v < 0 else ""
    a = abs(v)
    if compact:
        if cur.code == "INR":
            if a >= 1e7:
                return f"{sign}{cur.symbol}{a / 1e7:.2f} Cr"
            if a >= 1e5:
                return f"{sign}{cur.symbol}{a / 1e5:.2f} L"
            if a >= 1e3:
                return f"{sign}{cur.symbol}{a / 1e3:.1f}K"
        else:
            if a >= 1e6:
                return f"{sign}{cur.symbol}{a / 1e6:.2f}M"
            if a >= 1e3:
                return f"{sign}{cur.symbol}{a / 1e3:.1f}K"
    if cur.code == "INR":
        return f"{sign}{cur.symbol}{_indian_group(a, decimals)}"
    return f"{sign}{cur.symbol}{a:,.{decimals}f}"


def _indian_group(value: float, decimals: int = 0) -> str:
    """12,34,567 style digit grouping."""
    s = f"{value:.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return whole + (f".{frac}" if frac else "")


def pct(x: Optional[float], digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    return f"{x * 100:.{digits}f}%"


def num(x: Optional[float], digits: int = 0) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    return f"{x:,.{digits}f}"


def delta_text(current: Optional[float], previous: Optional[float]) -> Optional[str]:
    """Relative change as '+4.2%'; None when the comparison is not defined (zero or missing previous value)."""
    if current is None or previous is None or pd.isna(current) or pd.isna(previous) or previous == 0:
        return None
    return f"{(current - previous) / abs(previous) * 100:+.1f}%"


def points_delta(current: Optional[float], previous: Optional[float]) -> Optional[str]:
    """Change in a percentage metric, in percentage points."""
    if current is None or previous is None or pd.isna(current) or pd.isna(previous):
        return None
    return f"{(current - previous) * 100:+.1f} pts"


# ---------------------------------------------------------------------------- periods
PRESETS = ["Last month", "Last 3 months", "Last 6 months", "Last 12 months", "Year to date", "All data"]


def preset_range(preset: str, ref: pd.Timestamp, data_start: pd.Timestamp) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """Date range ending at ``ref`` (the reference date) for a named preset, clipped to the available data."""
    ref, data_start = pd.Timestamp(ref), pd.Timestamp(data_start)
    months = {"Last month": 1, "Last 3 months": 3, "Last 6 months": 6, "Last 12 months": 12}
    if preset in months:
        start = ref - pd.DateOffset(months=months[preset]) + pd.Timedelta(days=1)
    elif preset == "Year to date":
        start = pd.Timestamp(year=ref.year, month=1, day=1)
    else:
        start = data_start
    return max(start, data_start), ref


def comparison_range(start: pd.Timestamp, end: pd.Timestamp, mode: str, data_start: pd.Timestamp) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Comparison window for 'Previous period' or 'Same period last year'; None if it would start before the data."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if mode == "Same period last year":
        s, e = start - pd.DateOffset(years=1), end - pd.DateOffset(years=1)
    else:
        n = (end - start).days + 1
        s, e = start - pd.Timedelta(days=n), start - pd.Timedelta(days=1)
    if s < pd.Timestamp(data_start):
        return None
    return s, e


def is_whole_months(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    return pd.Timestamp(start).day == 1 and pd.Timestamp(end) == pd.Timestamp(end) + pd.offsets.MonthEnd(0)
