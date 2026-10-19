"""Action Center rules, the connected marketing->cash scenario, formatting helpers and a UI smoke test of every page."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from retailiq.analytics import actions as AC
from retailiq.analytics import cashflow as CF
from retailiq.analytics import marketing as MK
from retailiq.analytics import scenarios as SC
from retailiq.ui import formatting as FM

ROOT = Path(__file__).resolve().parents[1]


# ===================================================================== action center
@pytest.fixture(scope="module")
def findings(full_ds):
    return AC.run_all(full_ds, AC.Settings(min_cash_reserve=1_000_000))


def test_every_rule_returns_a_complete_traceable_finding(findings):
    assert len(findings) == len(AC.RULES)
    ids = [f.rule_id for f in findings]
    assert len(set(ids)) == len(ids)
    for f in findings:
        assert f.rule and f.reference and f.action and f.assumptions and f.page, f.rule_id
        assert f.numbers and all(isinstance(k, str) and isinstance(v, str) for k, v in f.numbers.items())
        assert f.severity in AC.SEVERITY_ORDER and f.title


def test_findings_are_sorted_with_triggered_first_and_most_severe_first(findings):
    flags = [f.triggered for f in findings]
    assert flags == sorted(flags, reverse=True)
    order = [AC.SEVERITY_ORDER[f.severity] for f in findings if f.triggered]
    assert order == sorted(order)


def test_known_simulated_issues_are_detected(findings):
    fired = {f.rule_id for f in findings if f.triggered}
    assert "R07" in fired       # repeated negative quality reviews (leaking bottle)
    assert "R05" in fired       # sales growing, operating profit falling
    assert "R06" in fired       # strong ROAS, weak contribution
    r07 = next(f for f in findings if f.rule_id == "R07")
    assert "Steel Water Bottle" in r07.numbers["Products"]


def test_integrity_rule_passes_on_consistent_data(findings):
    r13 = next(f for f in findings if f.rule_id == "R13")
    assert not r13.triggered


def test_cash_rule_fires_when_reserve_is_set_above_the_projected_low(full_ds):
    quiet = {f.rule_id: f for f in AC.run_all(full_ds, AC.Settings(min_cash_reserve=100_000))}
    loud = {f.rule_id: f for f in AC.run_all(full_ds, AC.Settings(min_cash_reserve=10_000_000))}
    assert not quiet["R01"].triggered and loud["R01"].triggered and loud["R01"].severity == "High"
    assert "Lowest projected cash" in loud["R01"].numbers and loud["R01"].detail is not None


def test_thresholds_control_whether_rules_fire(full_ds):
    off = {f.rule_id: f for f in AC.run_all(full_ds, AC.Settings(min_cash_reserve=1e6, slow_stock_value_threshold=1e12, strong_roas=99.0))}
    assert not off["R04"].triggered and not off["R06"].triggered


def test_rules_that_crash_do_not_hide_the_others(monkeypatch, small_ds):
    def broken(ds, s, ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(AC, "RULES", [broken] + AC.RULES[:2])
    out = AC.run_all(small_ds, AC.Settings())
    assert len(out) == 3 and any("could not run" in f.title for f in out)


# ===================================================================== integrated scenario
@pytest.fixture(scope="module")
def forecast(full_ds):
    return CF.forecast_cash(full_ds, CF.CashAssumptions(min_cash_reserve=1_000_000))


def _campaign(full_ds):
    c = full_ds.t["campaigns"].set_index("campaign_id").loc["C16"]
    return {"campaign_name": c["campaign_name"], "planned_budget": float(c["planned_budget"]), "start_date": c["start_date"], "end_date": c["end_date"], "target_category": None, "discount_pct": 0.1}


def test_scenario_chain_connects_spend_sales_stock_purchases_and_cash(full_ds, forecast):
    r = SC.marketing_cash_scenario(full_ds, forecast, _campaign(full_ds), 0.5, 2.5, 0.6, 0.25, 0.1, 1_000_000)
    assert r["valid"]
    assert r["extra_spend"] == pytest.approx(0.5 * 160_000)
    expected_attr = 160_000 * 2.5 * (1.5 ** 0.6 - 1)
    assert r["extra_attributed_revenue"] == pytest.approx(expected_attr)
    assert r["extra_revenue"] == pytest.approx(expected_attr * 0.75)
    assert r["purchase_cost"] > 0 and (r["stock"]["units_to_buy"] >= 0).all()
    assert (r["stock"]["units_to_buy"] % full_ds.t["products"].set_index("product_id").loc[r["stock"]["product_id"], "case_pack"].to_numpy() == 0).all()
    d = r["daily"]
    assert np.allclose(d["scenario_closing_cash"] - d["base_closing_cash"], d["overlay_flow"].cumsum())
    assert d["marketing_and_variable_costs"].sum() < 0 and d["purchases"].sum() <= 0 and d["collections"].sum() > 0
    assert list(r["chain"]["step"])[0].startswith("1.") and len(r["chain"]) == 6


def test_scenario_assumptions_drive_the_outcome_and_are_validated(full_ds, forecast):
    camp = _campaign(full_ds)
    low = SC.marketing_cash_scenario(full_ds, forecast, camp, 0.5, 2.5, 0.2, 0.25, 0.1, 1e6)
    high = SC.marketing_cash_scenario(full_ds, forecast, camp, 0.5, 2.5, 1.0, 0.25, 0.1, 1e6)
    assert high["extra_revenue"] > low["extra_revenue"] and high["purchase_cost"] >= low["purchase_cost"]
    none = SC.marketing_cash_scenario(full_ds, forecast, camp, 0.0, 2.5, 0.6, 0.25, 0.1, 1e6)
    assert none["extra_spend"] == 0 and none["extra_revenue"] == pytest.approx(0) and none["purchase_cost"] == 0
    for bad in [dict(spend_change_pct=-1.0), dict(elasticity=2.0), dict(baseline_share=1.0), dict(roas=-1.0)]:
        args = dict(spend_change_pct=0.5, roas=2.5, elasticity=0.6, baseline_share=0.25)
        args.update(bad)
        assert not SC.marketing_cash_scenario(full_ds, forecast, camp, **args)["valid"]


def test_funding_need_is_flagged_against_the_reserve(full_ds, forecast):
    r = SC.marketing_cash_scenario(full_ds, forecast, _campaign(full_ds), 1.0, 2.5, 0.6, 0.25, 0.1, 50_000_000)
    assert r["breach"] and r["funding_need"] == pytest.approx(50_000_000 - r["scenario_lowest_cash"], abs=1)
    ok = SC.marketing_cash_scenario(full_ds, forecast, _campaign(full_ds), 1.0, 2.5, 0.6, 0.25, 0.1, 0.0)
    assert not ok["breach"] and ok["funding_need"] == 0


# ===================================================================== formatting and periods
def test_indian_digit_grouping_and_compact_units():
    inr = FM.Currency("INR")
    assert FM.money(1234567, inr) == "₹12,34,567"
    assert FM.money(-150000, inr, compact=True) == "-₹1.50 L"
    assert FM.money(25000000, inr, compact=True) == "₹2.50 Cr"
    assert FM.money(None, inr) == "n/a" and FM.money(float("nan"), inr) == "n/a"


def test_other_currencies_are_converted_only_with_an_explicit_rate():
    usd = FM.Currency("USD", 0.012)
    assert FM.money(100000, usd) == "$1,200"
    assert FM.money(100000, FM.Currency("USD", 1.0)) == "$100,000"
    assert FM.Currency("INR").symbol == "₹"


def test_percent_and_delta_helpers_handle_missing_values():
    assert FM.pct(0.1234) == "12.3%" and FM.pct(None) == "n/a"
    assert FM.delta_text(110, 100) == "+10.0%" and FM.delta_text(5, 0) is None and FM.delta_text(None, 1) is None
    assert FM.points_delta(0.30, 0.25) == "+5.0 pts"


def test_period_presets_and_comparison_windows():
    ref, start = pd.Timestamp("2026-09-30"), pd.Timestamp("2024-10-01")
    s, e = FM.preset_range("Last 3 months", ref, start)
    assert (s, e) == (pd.Timestamp("2026-07-01"), ref) and FM.is_whole_months(s, e)
    assert FM.preset_range("All data", ref, start) == (start, ref)
    assert FM.preset_range("Last 12 months", ref, start)[0] == pd.Timestamp("2025-10-01")
    prev = FM.comparison_range(s, e, "Previous period", start)
    assert prev[1] == pd.Timestamp("2026-06-30") and (prev[1] - prev[0]).days == (e - s).days
    assert FM.comparison_range(s, e, "Same period last year", start) == (pd.Timestamp("2025-07-01"), pd.Timestamp("2025-09-30"))
    assert FM.comparison_range(start, ref, "Previous period", start) is None       # would begin before the data


# ===================================================================== UI smoke test
PAGES = ["overview", "action_center", "sales", "finance", "working_capital", "inventory", "cash_funding", "marketing", "customers", "reviews",
         "forecasting", "integrated_scenario", "data_definitions"]


@pytest.fixture()
def app_on_small_db(tmp_db, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from retailiq.ui import state

    monkeypatch.setattr(state, "DB_PATH", tmp_db)
    monkeypatch.chdir(ROOT)
    import streamlit as st

    st.cache_resource.clear()
    st.cache_data.clear()

    def make(page):
        at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=180)
        at.run()
        if page != "overview":
            at.switch_page(f"app_pages/{page}.py")
            at.run()
        return at

    return make


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_on_the_small_dataset_without_errors(app_on_small_db, page):
    at = app_on_small_db(page)
    assert not at.exception, [str(e.value)[:300] for e in at.exception]
    # st.error is also used for genuine business alerts (e.g. the small test business really does run out of cash);
    # anything else shown as an error is a handled failure and must not appear.
    business_alerts = ("Projected cash falls below the reserve", "With this scenario cash would fall below")
    unexpected = [str(e.value)[:300] for e in at.error if not any(a in str(e.value) for a in business_alerts)]
    assert not unexpected, unexpected
    assert at.title, "each page shows its title"


def test_app_offers_setup_when_there_is_no_database(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from retailiq.ui import state

    monkeypatch.setattr(state, "DB_PATH", tmp_path / "absent.db")
    monkeypatch.chdir(ROOT)
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("No RetailIQ database" in w.value for w in at.warning)
    assert any("Create simulated database" in b.label for b in at.button)


@pytest.mark.parametrize("page", ["overview", "inventory", "cash_funding", "working_capital", "integrated_scenario"])
def test_pages_work_in_a_non_inr_display_currency(app_on_small_db, page):
    from streamlit.testing.v1 import AppTest

    at = app_on_small_db("overview")
    at.session_state["currency"] = "USD"
    at.session_state["rate_USD"] = 0.0125
    at.run()
    if page != "overview":
        at.switch_page(f"app_pages/{page}.py")
        at.run()
    assert not at.exception, [str(e.value)[:300] for e in at.exception]
    shown = " ".join(str(m.value) for m in at.metric)
    assert "$" in shown and "₹" not in shown, "amounts must be displayed in the chosen currency, never mixed"
