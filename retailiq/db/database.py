"""Create, populate and read the RetailIQ SQLite database.

Design rules
------------
* Initialisation is repeatable: if the database already holds a dataset
  generated with the same seed and generator version, nothing is written.
* Existing data is never silently overwritten: ``reset=True`` is required,
  and the old file is copied to ``data/backups`` first.
* Reading returns pandas DataFrames with date columns parsed.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine

from ..config import BACKUP_DIR, DEFAULT_DB_PATH, GENERATOR_VERSION, GeneratorConfig
from .schema import DATE_COLUMNS, TABLE_ORDER, metadata


class DatabaseNotInitialisedError(RuntimeError):
    """Raised when the app is opened before the database has been created."""


def get_engine(db_path: Path | str = DEFAULT_DB_PATH) -> Engine:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def database_status(db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, str]:
    """Return the ``meta`` table as a dict, or an empty dict if the DB is missing/empty."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    engine = get_engine(db_path)
    try:
        if "meta" not in inspect(engine).get_table_names():
            return {}
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT key, value FROM meta")).all()
        return {k: v for k, v in rows}
    finally:
        engine.dispose()


def _backup(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"{db_path.stem}_{stamp}{db_path.suffix}"
    shutil.copy2(db_path, target)
    return target


def _to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to insertable dicts (NaN -> None, numpy -> python)."""
    clean = df.astype(object).where(pd.notna(df), None)
    records = clean.to_dict(orient="records")
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, np.generic):
                rec[k] = v.item()
    return records


def write_dataset(tables: Dict[str, pd.DataFrame], db_path: Path | str) -> None:
    """Create all tables and insert the generated dataset in a single transaction."""
    engine = get_engine(db_path)
    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            for name in TABLE_ORDER:
                df = tables[name]
                if df.empty:
                    continue
                table = metadata.tables[name]
                df = df[[c.name for c in table.columns]]
                records = _to_records(df)
                chunk = 5000
                for i in range(0, len(records), chunk):
                    conn.execute(table.insert(), records[i : i + chunk])
    finally:
        engine.dispose()


def initialise_database(
    db_path: Path | str = DEFAULT_DB_PATH,
    config: Optional[GeneratorConfig] = None,
    reset: bool = False,
    verbose: bool = True,
) -> str:
    """Generate the synthetic dataset and store it. Returns a status message.

    * Empty or missing database  -> generate and write.
    * Same seed/version present  -> do nothing (idempotent).
    * Different dataset present  -> refuse unless ``reset=True`` (then back up first).
    """
    from ..datagen.generator import generate_dataset  # local import keeps app start-up light

    config = config or GeneratorConfig()
    db_path = Path(db_path)
    status = database_status(db_path)

    if status and not reset:
        same = status.get("seed") == str(config.seed) and status.get("generator_version") == GENERATOR_VERSION
        if same and status.get("start_date") == config.start_date.isoformat() and status.get("months") == str(config.months):
            return f"Database already initialised at {db_path} (seed {config.seed}). Nothing was changed."
        raise RuntimeError(
            f"{db_path} already contains a different dataset (seed {status.get('seed')}, "
            f"version {status.get('generator_version')}). Re-run with --reset to back it up and regenerate."
        )

    if db_path.exists() and (status or reset):
        backup = _backup(db_path)
        if verbose:
            print(f"Existing database backed up to {backup}")
        db_path.unlink()
    elif db_path.exists():
        # File exists but has no meta table (e.g. empty file). Back up anyway, never delete silently.
        backup = _backup(db_path)
        if verbose:
            print(f"Existing file without RetailIQ data backed up to {backup}")
        db_path.unlink()

    if verbose:
        print("Generating simulated data ...")
    tables = generate_dataset(config)
    if verbose:
        for name in TABLE_ORDER:
            print(f"  {name:<22} {len(tables[name]):>8,} rows")
        print(f"Writing to {db_path} ...")
    write_dataset(tables, db_path)
    return f"Database initialised at {db_path} with seed {config.seed}."


def load_table(name: str, db_path: Path | str = DEFAULT_DB_PATH) -> pd.DataFrame:
    if name not in metadata.tables:
        raise KeyError(f"Unknown table {name!r}")
    db_path = Path(db_path)
    if not database_status(db_path):
        raise DatabaseNotInitialisedError(
            f"No RetailIQ data found at {db_path}. Run: python -m retailiq.cli init-db"
        )
    engine = get_engine(db_path)
    try:
        with engine.connect() as conn:
            df = pd.read_sql_table(name, conn)
    finally:
        engine.dispose()
    for col in DATE_COLUMNS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def load_all_tables(db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, pd.DataFrame]:
    db_path = Path(db_path)
    if not database_status(db_path):
        raise DatabaseNotInitialisedError(
            f"No RetailIQ data found at {db_path}. Run: python -m retailiq.cli init-db"
        )
    engine = get_engine(db_path)
    out: Dict[str, pd.DataFrame] = {}
    try:
        with engine.connect() as conn:
            for name in TABLE_ORDER:
                df = pd.read_sql_table(name, conn)
                for col in DATE_COLUMNS.get(name, []):
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], errors="coerce")
                out[name] = df
    finally:
        engine.dispose()
    return out


def orphan_check_sql() -> Dict[str, str]:
    """SQL queries that must return 0 rows if referential integrity holds."""
    return {
        "lines_without_transaction": "SELECT COUNT(*) FROM sales_lines l LEFT JOIN sales_transactions t ON l.transaction_id=t.transaction_id WHERE t.transaction_id IS NULL",
        "lines_with_unknown_product": "SELECT COUNT(*) FROM sales_lines l LEFT JOIN products p ON l.product_id=p.product_id WHERE p.product_id IS NULL",
        "transactions_unknown_customer": "SELECT COUNT(*) FROM sales_transactions t LEFT JOIN customers c ON t.customer_id=c.customer_id WHERE t.customer_id IS NOT NULL AND c.customer_id IS NULL",
        "transactions_unknown_campaign": "SELECT COUNT(*) FROM sales_transactions t LEFT JOIN campaigns c ON t.campaign_id=c.campaign_id WHERE t.campaign_id IS NOT NULL AND c.campaign_id IS NULL",
        "products_unknown_supplier": "SELECT COUNT(*) FROM products p LEFT JOIN suppliers s ON p.supplier_id=s.supplier_id WHERE s.supplier_id IS NULL",
        "reviews_unknown_transaction": "SELECT COUNT(*) FROM reviews r LEFT JOIN sales_transactions t ON r.transaction_id=t.transaction_id WHERE t.transaction_id IS NULL",
        "po_lines_unknown_po": "SELECT COUNT(*) FROM purchase_order_lines l LEFT JOIN purchase_orders p ON l.po_id=p.po_id WHERE p.po_id IS NULL",
    }


def run_orphan_checks(db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, int]:
    engine = get_engine(db_path)
    try:
        with engine.connect() as conn:
            return {name: conn.execute(text(sql)).scalar_one() for name, sql in orphan_check_sql().items()}
    finally:
        engine.dispose()
