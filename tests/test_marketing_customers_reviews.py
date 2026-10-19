"""Marketing metrics and attribution, customer analytics and review analysis."""
import numpy as np
import pandas as pd
import pytest

from retailiq.analytics import customers as CU
from retailiq.analytics import marketing as MK
from retailiq.analytics import reviews as RV


# ===================================================================== marketing
@pytest.fixture(scope="module")
def perf(small_ds):
    return MK.campaign_performance(small_ds)


def test_funnel_metrics_follow_their_definitions(small_ds, perf):
    cd = small_ds.t["campaign_daily"].groupby("campaign_id").agg(spend=("spend", "sum"), impressions=("impressions", "sum"), clicks=("clicks", "sum"))
    for r in perf.itertuples():
        raw = cd.loc[r.campaign_id]
        assert r.spend == pytest.approx(raw["spend"]) and r.clicks == raw["clicks"] and r.impressions == raw["impressions"]
        assert r.ctr == pytest.approx(raw["clicks"] / raw["impressions"])
        assert r.cpc == pytest.approx(raw["spend"] / raw["clicks"])
        assert r.conversion_rate == pytest.approx(r.orders / r.clicks)           # explicit denominator: clicks
        assert r.cpa == pytest.approx(r.spend / r.orders)
        assert r.roas == pytest.approx(r.attributed_revenue / r.spend)


def test_attributed_revenue_matches_transactions_and_is_not_duplicated(small_ds, perf):
    tx = small_ds.t["sales_transactions"]
    for cid, total in tx[tx["campaign_id"].notna()].groupby("campaign_id")["net_amount"].sum().items():
        if cid in set(perf["campaign_id"]):
            assert perf.set_index("campaign_id").loc[cid, "attributed_revenue"] == pytest.approx(total)
    chk = MK.attribution_check(small_ds)
    assert chk["within_total"] and chk["difference"] == pytest.approx(0, abs=0.01) and chk["max_campaigns_per_transaction"] == 1
    assert chk["attributed_by_transactions"] <= chk["total_revenue"]


def test_contribution_is_not_the_same_thing_as_roas(perf):
    c = perf
    recon = c["net_revenue_after_returns"] - c["cogs"] - c["variable_costs_est"] - c["spend"]
    assert np.allclose(c["contribution"], recon)
    # ROAS ignores product cost, so the two rankings can disagree
    assert (c["contribution"] < c["attributed_revenue"] - c["spend"]).all()


def test_baseline_share_only_changes_adjusted_contribution(small_ds):
    a = MK.campaign_performance(small_ds, baseline_share=0.0)
    b = MK.campaign_performance(small_ds, baseline_share=0.5)
    assert np.allclose(a["contribution"], b["contribution"])
    assert np.allclose(a["adjusted_contribution"], a["contribution"])
    assert (b["adjusted_contribution"] <= a["adjusted_contribution"] + 1e-6).all()
    with pytest.raises(ValueError):
        MK.campaign_performance(small_ds, baseline_share=1.0)


def test_zero_clicks_or_spend_give_none_not_infinity(small_ds):
    r = MK.campaign_performance(small_ds, small_ds.start, small_ds.start + pd.Timedelta(days=1))
    assert r.empty or r[["ctr", "cpc", "cpa", "roas"]].map(lambda v: v is None or np.isfinite(v)).all().all()


def test_channel_summary_recomputes_ratios_from_totals(perf):
    ch = MK.channel_summary(perf)
    for r in ch.itertuples():
        sub = perf[perf["channel"] == r.channel]
        assert r.roas == pytest.approx(sub["attributed_revenue"].sum() / sub["spend"].sum())
        assert r.ctr == pytest.approx(sub["clicks"].sum() / sub["impressions"].sum())


def test_budget_scenario_uses_diminishing_returns_and_validates(perf):
    ch = MK.channel_summary(perf)
    channel = ch["channel"].iloc[0]
    up = MK.marketing_budget_scenario(perf, {channel: 1.0}, elasticity=0.5)
    row = up[up["channel"] == channel].iloc[0]
    assert row["new_spend"] == pytest.approx(2 * row["current_spend"])
    assert row["expected_attributed_revenue"] == pytest.approx(row["current_attributed_revenue"] * 2 ** 0.5)
    assert row["revenue_change"] < row["current_attributed_revenue"]        # doubling spend does NOT double revenue
    flat = MK.marketing_budget_scenario(perf, {channel: 1.0}, elasticity=0.0)
    assert flat[flat["channel"] == channel]["revenue_change"].iloc[0] == pytest.approx(0)
    for bad in [lambda: MK.marketing_budget_scenario(perf, {channel: -1.0}), lambda: MK.marketing_budget_scenario(perf, {}, elasticity=3)]:
        with pytest.raises(ValueError):
            bad()


# ===================================================================== customers
def test_repeat_rate_matches_direct_calculation(small_ds):
    end = small_ds.end
    rm = CU.repeat_metrics(small_ds, end, window_days=365)
    tx = small_ds.t["sales_transactions"].dropna(subset=["customer_id"])
    w = tx[(tx["transaction_date"] >= rm["window_start"]) & (tx["transaction_date"] <= end)]
    per = w.groupby("customer_id")["transaction_id"].nunique()
    assert rm["active_customers"] == len(per) and rm["repeat_customers"] == (per >= 2).sum()
    assert rm["repeat_rate"] == pytest.approx((per >= 2).mean())
    assert rm["purchase_frequency"] == pytest.approx(per.mean())
    assert rm["avg_order_value"] == pytest.approx(w["net_amount"].mean())
    assert 0 < rm["identified_revenue_share"] < 1


def test_new_and_returning_customers_partition_active_customers(small_ds):
    nv = CU.new_vs_returning(small_ds)
    assert (nv["new_customers"] + nv["returning_customers"] == nv["active_customers"]).all()
    assert (nv["new_customers"] >= 0).all() and (nv["returning_customers"] >= 0).all()
    fp = CU.first_purchase_table(small_ds)
    new_total = int(fp["is_new_customer"].sum())
    assert nv["new_customers"].sum() <= new_total + 1


def test_rfm_scores_statuses_and_thresholds(small_ds):
    rfm = CU.rfm_table(small_ds, at_risk_days=60, inactive_days=120)
    active = rfm[rfm["frequency"] > 0]
    assert active[["R", "F", "M"]].stack().between(1, 5).all()
    assert (rfm.loc[rfm["status"] == "Inactive", "recency_days"] >= 120).all()
    assert rfm.loc[rfm["status"] == "Becoming inactive", "recency_days"].between(60, 119).all()
    assert (rfm.loc[rfm["status"] == "Active", "recency_days"] < 60).all()
    assert (rfm.loc[rfm["frequency"] == 0, "segment"] == "Lapsed (no purchase in window)").all()
    strict = CU.rfm_table(small_ds, at_risk_days=10, inactive_days=20)
    assert (strict["status"] == "Inactive").sum() >= (rfm["status"] == "Inactive").sum()


def test_cohort_retention_starts_at_one_and_hides_unobservable_months(small_ds):
    coh = CU.cohort_retention(small_ds)
    assert (coh[0] == 1.0).all()
    values = coh.drop(columns="cohort_size")
    assert ((values.dropna(how="all", axis=1) <= 1.0 + 1e-9) | values.isna()).all().all()
    last = coh.index[-1]
    assert values.loc[last].iloc[1:].isna().all(), "the newest cohort cannot have observed future months"


def test_lifetime_value_requires_24_months_and_states_its_method(small_ds, full_ds):
    short = CU.lifetime_value(small_ds)
    assert not short["valid"] and "24 months" in short["reason"]
    lv = CU.lifetime_value(full_ds)
    assert lv["valid"]
    assert lv["clv"] == pytest.approx(lv["margin_per_year"] * lv["expected_lifetime_years"])
    assert lv["expected_lifetime_years"] <= 5.0
    assert lv["margin_per_year"] == pytest.approx(lv["orders_per_year"] * lv["avg_order_value"] * lv["gross_margin"])
    assert lv["annual_churn"] == pytest.approx(1 - lv["annual_retention"])
    assert "Limitations" in lv["method"]


# ===================================================================== reviews
def test_sentiment_baseline_handles_polarity_and_negation():
    assert RV.sentiment_score("Excellent quality, works perfectly.")["label"] == "positive"
    assert RV.sentiment_score("Poor quality, it broke after a week.")["label"] == "negative"
    assert RV.sentiment_score("This is not good at all")["label"] == "negative"
    assert RV.sentiment_score("not bad")["label"] == "positive"
    assert RV.sentiment_score("Bought it on Tuesday")["label"] == "neutral"
    r = RV.sentiment_score("Great but poor delivery")
    assert "great" in r["positive_words"] and "poor" in r["negative_words"]


def test_theme_detection_allows_several_themes():
    t = RV.detect_themes("Too expensive and the delivery was late, but staff were helpful")
    assert {"price", "delivery", "service"} <= set(t)
    assert RV.detect_themes("Nothing to say") == []
    assert "quality" in RV.detect_themes("The lid is leaking, poor quality")


def test_rating_summary_and_distribution(small_ds):
    rv = RV.analysed_reviews(small_ds)
    s = RV.rating_summary(rv)
    assert s["count"] == len(rv) and s["avg_rating"] == pytest.approx(rv["rating"].mean())
    assert s["distribution"].sum() == len(rv) and list(s["distribution"].index) == [1, 2, 3, 4, 5]
    empty = RV.rating_summary(rv.iloc[0:0])
    assert empty["count"] == 0 and empty["avg_rating"] is None


def test_search_filters_and_baseline_agreement_is_reported_not_overstated(small_ds):
    rv = RV.analysed_reviews(small_ds)
    hits = RV.search_reviews(rv, "late")
    assert len(hits) > 0 and hits["review_text"].str.contains("late", case=False).all()
    neg = RV.search_reviews(rv, sentiments=["negative"])
    assert (neg["sentiment"] == "negative").all()
    ag = RV.baseline_agreement(rv)
    assert 0 <= ag["agreement"] <= 1 and ag["matrix"].values.sum() == len(rv)
    assert RV.baseline_agreement(rv.iloc[0:0])["agreement"] is None


def test_repeated_complaints_and_declining_products_use_their_stated_rules(full_ds):
    rv = RV.analysed_reviews(full_ds)
    rep = RV.repeated_complaints(full_ds, rv, min_count=5, min_share=0.25)
    assert (rep["negative_mentions"] >= 5).all() and (rep["share_of_product_reviews"] >= 0.25).all()
    assert ((rep["product_id"] == "P05") & (rep["theme"] == "quality")).any(), "the simulated leaking-bottle defect should be found"
    dec = RV.declining_products(full_ds, rv)
    assert (dec["recent_reviews"] >= 8).all() and (dec["prior_reviews"] >= 8).all()
    assert (dec["flag"] == ((dec["prior_avg"] - dec["recent_avg"]) >= 0.4)).all()
