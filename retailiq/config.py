"""Central configuration for RetailIQ.

Everything that a beginner might want to tweak lives here: file locations,
the random seed, the simulated period and display-currency defaults.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
BACKUP_DIR = DATA_DIR / "backups"
EXPORT_DIR = DATA_DIR / "exports"

# The database location can be overridden with the RETAILIQ_DB environment variable.
DEFAULT_DB_PATH = Path(os.environ.get("RETAILIQ_DB", DATA_DIR / "retailiq.db"))

GENERATOR_VERSION = "1.0.0"

# All monetary values are stored in this currency. Display conversion happens in the UI only.
BASE_CURRENCY = "INR"
CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "AED": "AED "}


@dataclass(frozen=True)
class GeneratorConfig:
    """Parameters for the synthetic data generator.

    The defaults produce ~24 months of data for two stores. Tests use a smaller
    configuration (fewer months/customers) so they run quickly.
    """

    seed: int = 42
    start_date: dt.date = dt.date(2024, 10, 1)
    months: int = 24
    n_retail_customers: int = 1200
    n_existing_customers: int = 300  # customers who registered before the start date
    n_business_customers: int = 40
    demand_scale: float = 1.0  # multiplies store traffic; use < 1 for faster tests

    @property
    def end_date(self) -> dt.date:
        """Last simulated day (inclusive)."""
        year = self.start_date.year + (self.start_date.month - 1 + self.months) // 12
        month = (self.start_date.month - 1 + self.months) % 12 + 1
        return dt.date(year, month, 1) - dt.timedelta(days=1)


DEFAULT_CONFIG = GeneratorConfig()


@dataclass(frozen=True)
class AnalysisDefaults:
    """Default thresholds used by analytics and the Action Center (all editable in the UI)."""

    minimum_cash_reserve: float = 750_000.0
    service_level_z: float = 1.65  # ~95% cycle service level under a normal approximation
    review_period_days: int = 7
    target_cover_days: int = 30
    slow_moving_days_of_stock: int = 120
    low_stock_buffer_days: int = 0
    overstock_days_of_stock: int = 120
    at_risk_days: int = 60
    inactive_days: int = 120
    strong_roas: float = 3.0
    negative_review_min_count: int = 5


ANALYSIS_DEFAULTS = AnalysisDefaults()
