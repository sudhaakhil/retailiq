"""Synthetic data generation and the database layer: reproducibility, consistency and safe initialisation."""
import datetime as dt

import pandas as pd
import pytest

from retailiq.config import GeneratorConfig
from retailiq.datagen.generator import generate_dataset
from retailiq.db import database_status, initialise_database, load_all_tables, load_table
from retailiq.db.database import run_orphan_checks
from retailiq.db.schema import TABLE_ORDER, metadata


def _frames_equal(a, b):
    for name in TABLE_ORDER:
        pd.testing.assert_frame_equal(a[name].reset_index(drop=True), b[name].reset_index(drop=True), check_exact=True, obj=name)


def test_generation_is_reproducible_for_a_fixed_seed(small_config):
    first = generate_dataset(small_config)
    second = generate_dataset(small_config)
    _frames_equal(first, second)


def test_different_seed_gives_different_data(small_config, small_tables):
    other = generate_dataset(GeneratorConfig(**{**small_config.__dict__, "seed": small_config.seed + 1}))
    assert not other["sales_transactions"]["net_amount"].equals(small_tables["sales_transactions"]["net_amount"])


def test_shape_of_the_dataset(small_tables):
    t = small_tables
    assert len(t["stores"]) == 2
    assert len(t["products"]) == 20 and t["products"]["category"].nunique() >= 5
    assert len(t["suppliers"]) >= 5 and (t["suppliers"]["lead_time_days"] > 0).all()
    assert t["customers"]["customer_type"].isin(["retail", "business"]).all()
    assert t["meta"].set_index("key").loc["simulated", "value"] == "true"
    assert set(TABLE_ORDER) == set(metadata.tables) == set(t)


def test_period_is_about_the_configured_number_of_months(small_config, small_tables):
    d = pd.to_datetime(small_tables["sales_transactions"]["transaction_date"])
    assert d.min() == pd.Timestamp(small_config.start_date)
    assert d.max() <= pd.Timestamp(small_config.end_date)
    assert small_config.end_date == dt.date(2026, 8, 31)


def test_default_configuration_covers_24_months(full_tables):
    d = pd.to_datetime(full_tables["sales_transactions"]["transaction_date"])
    assert (d.max() - d.min()).days >= 700


def test_transaction_totals_reconcile_with_line_items(small_tables):
    tx, ln = small_tables["sales_transactions"], small_tables["sales_lines"]
    g = ln.groupby("transaction_id").agg(net=("net_amount", "sum"), disc=("discount_amount", "sum"))
    m = tx.set_index("transaction_id").join(g)
    assert (m["net_amount"] - m["net"]).abs().max() < 0.005
    assert (m["discount_amount"] - m["disc"]).abs().max() < 0.005
    assert (tx["gross_amount"] - tx["discount_amount"] - tx["net_amount"]).abs().max() < 0.005
    assert set(ln["transaction_id"]) <= set(tx["transaction_id"])


def test_line_gross_equals_quantity_times_price(small_ds):
    L = small_ds.lines
    assert ((L["quantity"] * L["unit_price"]).round(2) - (L["net_amount"] + L["discount_amount"])).abs().max() < 0.005


def test_inventory_movements_reconcile_with_balances(small_tables):
    mv, inv = small_tables["stock_movements"], small_tables["inventory_daily"]
    last = inv[inv["balance_date"] == inv["balance_date"].max()].set_index(["store_id", "product_id"])
    calc = mv.groupby(["store_id", "product_id"]).agg(q=("quantity", "sum"), v=("value", "sum"))
    j = last.join(calc)
    assert (j["quantity_on_hand"] - j["q"]).abs().max() == 0
    assert (j["inventory_value"] - j["v"]).abs().max() < 0.01
    assert (inv["quantity_on_hand"] >= 0).all()


def test_daily_inventory_balance_equals_cumulative_movements(small_tables):
    mv, inv = small_tables["stock_movements"], small_tables["inventory_daily"]
    pick = inv[(inv["store_id"] == "S01") & (inv["product_id"] == "P09")].set_index("balance_date")["quantity_on_hand"]
    m = mv[(mv["store_id"] == "S01") & (mv["product_id"] == "P09")].groupby("movement_date")["quantity"].sum().cumsum()
    m = m.reindex(pick.index, method="ffill")
    assert (pick - m).abs().max() == 0


def test_cash_ledger_matches_independent_rebuild(small_ds):
    from retailiq.analytics import finance as F

    for d in [small_ds.start + pd.Timedelta(days=100), small_ds.end]:
        assert abs(F.cash_balance_at(small_ds, d) - F.independent_cash_balance(small_ds, d)) < 0.01


def test_identifiers_are_valid(small_tables):
    t = small_tables
    assert set(t["sales_lines"]["product_id"]) <= set(t["products"]["product_id"])
    assert set(t["products"]["supplier_id"]) <= set(t["suppliers"]["supplier_id"])
    assert set(t["sales_transactions"]["customer_id"].dropna()) <= set(t["customers"]["customer_id"])
    assert set(t["sales_transactions"]["campaign_id"].dropna()) <= set(t["campaigns"]["campaign_id"])
    assert set(t["reviews"]["transaction_id"]) <= set(t["sales_transactions"]["transaction_id"])
    assert set(t["reviews"]["customer_id"]) <= set(t["customers"]["customer_id"])
    assert set(t["purchase_order_lines"]["po_id"]) <= set(t["purchase_orders"]["po_id"])
    assert set(t["returns"]["line_id"]) <= set(t["sales_lines"]["line_id"])
    assert set(t["campaign_daily"]["campaign_id"]) <= set(t["campaigns"]["campaign_id"])


def test_returns_do_not_exceed_quantities_sold(small_tables):
    r = small_tables["returns"].merge(small_tables["sales_lines"][["line_id", "quantity", "net_amount"]], on="line_id", suffixes=("_ret", "_sold"))
    assert (r["quantity_ret"] <= r["quantity_sold"]).all()
    assert (r["refund_amount"] <= r["net_amount"] + 0.005).all()
    assert small_tables["returns"]["line_id"].is_unique


def test_each_sale_has_at_most_one_campaign_and_attribution_does_not_exceed_revenue(small_tables):
    tx = small_tables["sales_transactions"]
    assert tx["transaction_id"].is_unique
    assert tx.loc[tx["campaign_id"].notna(), "net_amount"].sum() <= tx["net_amount"].sum()


def test_supplier_payments_never_exceed_invoices(small_tables):
    inv = small_tables["supplier_invoices"].set_index("invoice_id")["amount"]
    paid = small_tables["supplier_payments"].groupby("invoice_id")["amount"].sum()
    assert (paid <= inv.reindex(paid.index) + 0.005).all()


def test_loan_instalments_split_interest_from_principal(small_tables):
    lp = small_tables["loan_payments"]
    assert (lp["interest_amount"] >= 0).all() and (lp["principal_amount"] > 0).all()
    ledger = small_tables["cash_ledger"]
    assert ledger["category"].isin(["Loan interest", "Loan principal repayment"]).any()
    assert not ledger.loc[ledger["category"] == "Loan principal repayment", "activity"].ne("financing").any()
    assert not ledger.loc[ledger["category"].str.startswith("Operating expenses"), "activity"].ne("operating").any()


def test_opening_balances_documented(small_tables):
    ob = small_tables["opening_balances"].set_index("item")["amount"]
    assert {"cash", "accounts_receivable", "inventory", "fixed_assets_net", "accounts_payable", "loan_principal", "owner_equity"} <= set(ob.index)
    assets = ob[["cash", "accounts_receivable", "inventory", "fixed_assets_net"]].sum()
    assert abs(assets - ob[["accounts_payable", "loan_principal", "owner_equity"]].sum()) < 0.01


# ------------------------------------------------------------------- database layer
def test_initialise_creates_database_and_is_repeatable(tmp_path, small_config, monkeypatch):
    from retailiq.db import database

    monkeypatch.setattr(database, "BACKUP_DIR", tmp_path / "bk")
    db = tmp_path / "r.db"
    msg1 = initialise_database(db, small_config, verbose=False)
    before = {n: len(df) for n, df in load_all_tables(db).items()}
    msg2 = initialise_database(db, small_config, verbose=False)
    after = {n: len(df) for n, df in load_all_tables(db).items()}
    assert "initialised" in msg1 and "Nothing was changed" in msg2
    assert before == after
    assert database_status(db)["seed"] == str(small_config.seed)


def test_different_dataset_is_never_silently_overwritten(tmp_path, small_config, monkeypatch):
    from retailiq.db import database

    monkeypatch.setattr(database, "BACKUP_DIR", tmp_path / "bk")
    db = tmp_path / "r.db"
    initialise_database(db, small_config, verbose=False)
    other = GeneratorConfig(**{**small_config.__dict__, "seed": 99})
    with pytest.raises(RuntimeError):
        initialise_database(db, other, verbose=False)
    assert database_status(db)["seed"] == str(small_config.seed)
    initialise_database(db, other, reset=True, verbose=False)
    assert database_status(db)["seed"] == "99"
    assert list((tmp_path / "bk").glob("*.db")), "the old database must be backed up before reset"


def test_load_table_parses_dates_and_unknown_table_fails(tmp_db):
    df = load_table("sales_transactions", tmp_db)
    assert pd.api.types.is_datetime64_any_dtype(df["transaction_date"])
    with pytest.raises(KeyError):
        load_table("no_such_table", tmp_db)


def test_missing_database_gives_clear_error(tmp_path):
    from retailiq.db import DatabaseNotInitialisedError

    with pytest.raises(DatabaseNotInitialisedError):
        load_table("stores", tmp_path / "missing.db")
    assert database_status(tmp_path / "missing.db") == {}


def test_database_has_no_orphans_and_foreign_keys_are_enforced(tmp_db):
    assert all(v == 0 for v in run_orphan_checks(tmp_db).values())
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from retailiq.db import get_engine

    eng = get_engine(tmp_db)
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO sales_lines (line_id, transaction_id, product_id, quantity, unit_price, discount_amount, net_amount, unit_cost, cogs_amount) "
                              "VALUES ('X', 'NOPE', 'P01', 1, 1, 0, 1, 1, 1)"))


def test_every_table_documents_its_grain():
    for name, t in metadata.tables.items():
        assert t.info.get("grain"), f"{name} is missing a grain description"
