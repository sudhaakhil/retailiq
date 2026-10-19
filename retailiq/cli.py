"""Command-line tools:  python -m retailiq.cli <command>

  init-db     create the simulated database (safe to repeat; never overwrites without --reset)
  check       run the reconciliation and integrity checks on the database
  export      write every table to CSV files
  info        show what the database contains
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .config import DEFAULT_DB_PATH, EXPORT_DIR, GeneratorConfig


def _utf8_stdout() -> None:
    """Windows consoles often use a legacy code page that cannot print the rupee sign."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def cmd_init(args: argparse.Namespace) -> int:
    from .db import initialise_database

    cfg = GeneratorConfig(seed=args.seed, months=args.months, start_date=dt.date.fromisoformat(args.start))
    try:
        print(initialise_database(Path(args.db), cfg, reset=args.reset))
    except RuntimeError as exc:
        print(f"Not changed: {exc}")
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .analytics import finance as F
    from .analytics import inventory as INV
    from .analytics.dataset import Dataset
    from .db import database_status
    from .db.database import run_orphan_checks

    if not database_status(Path(args.db)):
        print("No database found. Run: python -m retailiq.cli init-db")
        return 1
    ds = Dataset.load(args.db)
    failures = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    for name, n in run_orphan_checks(args.db).items():
        report(f"referential integrity - {name}", n == 0, f"{n} orphan rows")
    tx = ds.t["sales_transactions"]
    line_net = ds.lines.groupby("transaction_id")["net_amount"].sum()
    diff = (tx.set_index("transaction_id")["net_amount"] - line_net).abs().max()
    report("transaction totals equal the sum of their lines", diff < 0.01, f"largest difference {diff:.4f}")
    rec = INV.reconcile_movements(ds)
    report("stock movements reconcile with stock balances", rec["qty_diff"].abs().max() == 0 and rec["value_diff"].abs().max() < 0.01,
           f"largest quantity difference {rec['qty_diff'].abs().max():.0f}, value difference {rec['value_diff'].abs().max():.2f}")
    for d in ((ds.start + (ds.end - ds.start) / 2).normalize(), ds.end):
        a, b = F.cash_balance_at(ds, d), F.independent_cash_balance(ds, d)
        report(f"cash ledger equals cash rebuilt from source tables ({d:%Y-%m-%d})", abs(a - b) < 0.01, f"{a:,.2f} vs {b:,.2f}")
        bs = F.balance_sheet(ds, d)
        report(f"balance sheet balances ({d:%Y-%m-%d})", abs(bs["difference"]) < 0.01, f"assets - liabilities - equity = {bs['difference']:.2f}")
    print("\nAll checks passed." if failures == 0 else f"\n{failures} check(s) FAILED.")
    return 0 if failures == 0 else 2


def cmd_export(args: argparse.Namespace) -> int:
    from .db import load_all_tables

    out = Path(args.out or EXPORT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in load_all_tables(args.db).items():
        df.to_csv(out / f"{name}.csv", index=False)
    print(f"Wrote {len(load_all_tables(args.db))} CSV files to {out}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    from .db import database_status, load_all_tables

    status = database_status(Path(args.db))
    if not status:
        print("No database found. Run: python -m retailiq.cli init-db")
        return 1
    print("SIMULATED DATA - not real business data")
    for k, v in status.items():
        print(f"  {k}: {v}")
    for name, df in load_all_tables(args.db).items():
        print(f"  {name:<24}{len(df):>9,} rows")
    return 0


def main(argv=None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(prog="python -m retailiq.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="database file (default: data/retailiq.db)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init-db", help="create the simulated database")
    p.add_argument("--seed", type=int, default=GeneratorConfig().seed)
    p.add_argument("--months", type=int, default=GeneratorConfig().months)
    p.add_argument("--start", default=GeneratorConfig().start_date.isoformat(), help="first simulated day, YYYY-MM-DD")
    p.add_argument("--reset", action="store_true", help="replace an existing database (the old file is backed up first)")
    p.set_defaults(func=cmd_init)
    sub.add_parser("check", help="run reconciliation checks").set_defaults(func=cmd_check)
    e = sub.add_parser("export", help="export tables to CSV")
    e.add_argument("--out", help="output folder (default: data/exports)")
    e.set_defaults(func=cmd_export)
    sub.add_parser("info", help="describe the database").set_defaults(func=cmd_info)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
