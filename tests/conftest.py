"""Shared fixtures. Datasets are generated once per test session.

* ``small_tables`` / ``small_ds``: 14 months, reduced traffic - fast, used by most tests. Fixed costs are NOT scaled down,
  so this small business is loss-making and its cash balance can be negative; tests must not assume positive cash.
* ``full_tables`` / ``full_ds``: the default 24-month configuration - used where 24 months of history are required.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retailiq.analytics.dataset import Dataset  # noqa: E402
from retailiq.config import GeneratorConfig  # noqa: E402
from retailiq.datagen.generator import generate_dataset  # noqa: E402

SMALL_CONFIG = GeneratorConfig(seed=11, start_date=dt.date(2025, 7, 1), months=14, demand_scale=0.35, n_retail_customers=250,
                               n_existing_customers=60, n_business_customers=12)


@pytest.fixture(scope="session")
def small_config():
    return SMALL_CONFIG


@pytest.fixture(scope="session")
def small_tables():
    return generate_dataset(SMALL_CONFIG)


@pytest.fixture(scope="session")
def small_ds(small_tables):
    return Dataset.from_tables(small_tables).warm()


@pytest.fixture(scope="session")
def full_tables():
    return generate_dataset(GeneratorConfig())


@pytest.fixture(scope="session")
def full_ds(full_tables):
    return Dataset.from_tables(full_tables).warm()


@pytest.fixture()
def tmp_db(tmp_path, small_tables):
    """A real SQLite file populated with the small dataset."""
    from retailiq.db.database import write_dataset

    path = tmp_path / "test.db"
    write_dataset(small_tables, path)
    return path
