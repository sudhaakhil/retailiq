"""Customer review analysis with a transparent, rule-based baseline.

Sentiment baseline
  Score = (positive word hits - negative word hits) / max(1, total hits), with simple negation handling
  ("not good" counts as negative). Label: score > 0.2 positive, < -0.2 negative, otherwise neutral.
  It is a dictionary method - fast and inspectable, but it misses sarcasm, context and words not in the lists.

IMPORTANT: the review text in this project is SIMULATED from templates. Agreement with the star rating is
therefore inflated and says nothing about how the method would perform on real reviews.

Themes
  A review can mention several themes. A theme is detected when one of its keywords appears in the text.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .accounting import safe_div
from .dataset import Dataset, between

POSITIVE = {"excellent", "great", "good", "happy", "perfect", "perfectly", "sturdy", "helpful", "friendly", "quick", "quickly",
            "affordable", "worth", "recommend", "polite", "love", "amazing", "lasted", "well", "fair", "reasonably", "fast", "nice",
            "best", "satisfied", "smooth", "comfortable"}
NEGATIVE = {"poor", "bad", "cheap", "flimsy", "broke", "stopped", "disappointed", "late", "rude", "leaking", "leaks", "leak", "damaged",
            "defective", "unavailable", "frustrating", "expensive", "overpriced", "unhelpful", "worst", "waste", "slow", "broken",
            "complaint", "problem", "faulty", "spilled", "angry", "annoying", "queue", "longer", "unhappy"}
NEGATIONS = {"not", "no", "never", "n't", "without", "hardly"}
THEMES: Dict[str, List[str]] = {
    "price": ["price", "priced", "pricing", "expensive", "overpriced", "discount", "value for money", "affordable", "offer", "cost"],
    "quality": ["quality", "sturdy", "flimsy", "material", "build", "broke", "stopped working", "durable", "lasted", "defective",
                "leak", "leaking", "leaks", "seal", "feels cheap", "works perfectly"],
    "delivery": ["delivery", "delivered", "package", "packed", "arrived", "courier", "shipping", "box"],
    "availability": ["out of stock", "in stock", "stock", "unavailable", "available", "come back again", "running out"],
    "service": ["staff", "service", "billing", "queue", "counter", "helpful", "rude", "polite", "friendly"],
}
TOKEN = re.compile(r"[a-z']+")


def sentiment_score(text: str) -> Dict:
    """Explainable sentiment: which positive/negative words were found and the resulting label."""
    tokens = TOKEN.findall(str(text).lower())
    pos_hits, neg_hits = [], []
    for i, tok in enumerate(tokens):
        window = tokens[max(0, i - 2): i]
        negated = any(w in NEGATIONS or w.endswith("n't") for w in window)
        if tok in POSITIVE:
            (neg_hits if negated else pos_hits).append(("not " if negated else "") + tok)
        elif tok in NEGATIVE:
            (pos_hits if negated else neg_hits).append(("not " if negated else "") + tok)
    total = len(pos_hits) + len(neg_hits)
    score = (len(pos_hits) - len(neg_hits)) / max(1, total)
    label = "positive" if score > 0.2 else ("negative" if score < -0.2 else "neutral")
    return {"score": score, "label": label, "positive_words": ", ".join(pos_hits), "negative_words": ", ".join(neg_hits), "hits": total}


def detect_themes(text: str) -> List[str]:
    t = str(text).lower()
    return [name for name, kws in THEMES.items() if any(k in t for k in kws)]


def star_label(rating: int) -> str:
    return "positive" if rating >= 4 else ("negative" if rating <= 2 else "neutral")


def analysed_reviews(ds: Dataset) -> pd.DataFrame:
    """Reviews with sentiment, themes, product/store names (grain: review)."""
    rv = ds.t["reviews"].copy()
    if rv.empty:
        return rv.assign(sentiment="", sentiment_score=0.0, themes="", star_sentiment="", product_name="", category="", store_name="")
    sc = [sentiment_score(t) for t in rv["review_text"]]
    rv["sentiment"] = [s["label"] for s in sc]
    rv["sentiment_score"] = [s["score"] for s in sc]
    rv["positive_words"] = [s["positive_words"] for s in sc]
    rv["negative_words"] = [s["negative_words"] for s in sc]
    rv["theme_list"] = [detect_themes(t) for t in rv["review_text"]]
    rv["themes"] = [", ".join(x) for x in rv["theme_list"]]
    rv["star_sentiment"] = rv["rating"].map(star_label)
    rv["product_name"] = rv["product_id"].map(ds.product_names)
    rv["category"] = rv["product_id"].map(ds.product_category)
    rv["store_name"] = rv["store_id"].map(ds.store_names)
    rv["month"] = rv["review_date"].dt.to_period("M").dt.start_time
    return rv


def baseline_agreement(rv: pd.DataFrame) -> Dict:
    """How often the text baseline matches the star-rating label (NOT a measure of true accuracy - see module note)."""
    if rv.empty:
        return {"n": 0, "agreement": None, "matrix": pd.DataFrame()}
    matrix = pd.crosstab(rv["star_sentiment"], rv["sentiment"])
    return {"n": int(len(rv)), "agreement": float((rv["star_sentiment"] == rv["sentiment"]).mean()), "matrix": matrix}


def rating_summary(rv: pd.DataFrame) -> Dict:
    if rv.empty:
        return {"count": 0, "avg_rating": None, "distribution": pd.Series(dtype=int)}
    dist = rv["rating"].value_counts().reindex([1, 2, 3, 4, 5], fill_value=0)
    return {"count": int(len(rv)), "avg_rating": float(rv["rating"].mean()), "distribution": dist}


def rating_trend(rv: pd.DataFrame, by: str = "product_name") -> pd.DataFrame:
    """Monthly review count and average rating by product, category or store."""
    if rv.empty:
        return pd.DataFrame(columns=["month", by, "reviews", "avg_rating"])
    return rv.groupby(["month", by], as_index=False).agg(reviews=("review_id", "count"), avg_rating=("rating", "mean"))


def theme_table(rv: pd.DataFrame) -> pd.DataFrame:
    """Mentions per theme and sentiment (a review can contribute to several themes)."""
    rows = []
    for r in rv.itertuples():
        for th in r.theme_list:
            rows.append({"theme": th, "sentiment": r.sentiment, "rating": r.rating})
    if not rows:
        return pd.DataFrame(columns=["theme", "mentions", "avg_rating", "negative_share"])
    d = pd.DataFrame(rows)
    g = d.groupby("theme").agg(mentions=("rating", "size"), avg_rating=("rating", "mean"),
                               negative_share=("sentiment", lambda s: float((s == "negative").mean()))).reset_index()
    return g.sort_values("mentions", ascending=False)


def declining_products(ds: Dataset, rv: pd.DataFrame, end=None, recent_days: int = 90, prior_days: int = 180,
                       min_reviews: int = 8, min_drop: float = 0.4) -> pd.DataFrame:
    """Products whose average rating fell by at least ``min_drop`` between the prior and the recent window."""
    end = ds.ref_date if end is None else pd.Timestamp(end)
    r_start = end - pd.Timedelta(days=recent_days - 1)
    p_start, p_end = r_start - pd.Timedelta(days=prior_days), r_start - pd.Timedelta(days=1)
    rows = []
    for pid, g in rv.groupby("product_id"):
        rec, pri = g[between(g["review_date"], r_start, end)], g[between(g["review_date"], p_start, p_end)]
        if len(rec) < min_reviews or len(pri) < min_reviews:
            continue
        drop = pri["rating"].mean() - rec["rating"].mean()
        rows.append({"product_id": pid, "product_name": ds.product_names.get(pid, pid), "recent_reviews": len(rec), "prior_reviews": len(pri),
                     "recent_avg": rec["rating"].mean(), "prior_avg": pri["rating"].mean(), "change": -drop, "flag": drop >= min_drop})
    out = pd.DataFrame(rows, columns=["product_id", "product_name", "recent_reviews", "prior_reviews", "recent_avg", "prior_avg", "change", "flag"])
    return out.sort_values("change").reset_index(drop=True)


def repeated_complaints(ds: Dataset, rv: pd.DataFrame, end=None, days: int = 120, min_count: int = 5, min_share: float = 0.25) -> pd.DataFrame:
    """Product x theme pairs mentioned in >= ``min_count`` negative reviews recently and >= ``min_share`` of the product's reviews."""
    end = ds.ref_date if end is None else pd.Timestamp(end)
    w = rv[between(rv["review_date"], end - pd.Timedelta(days=days - 1), end)]
    totals = w.groupby("product_id").size()
    rows = []
    for r in w[(w["rating"] <= 2) | (w["sentiment"] == "negative")].itertuples():
        for th in r.theme_list:
            rows.append((r.product_id, th, r.review_id))
    if not rows:
        return pd.DataFrame(columns=["product_id", "product_name", "theme", "negative_mentions", "share_of_product_reviews"])
    d = pd.DataFrame(rows, columns=["product_id", "theme", "review_id"]).drop_duplicates()
    g = d.groupby(["product_id", "theme"]).size().rename("negative_mentions").reset_index()
    g["product_reviews"] = g["product_id"].map(totals)
    g["share_of_product_reviews"] = g["negative_mentions"] / g["product_reviews"]
    g["product_name"] = g["product_id"].map(ds.product_names)
    g = g[(g["negative_mentions"] >= min_count) & (g["share_of_product_reviews"] >= min_share)]
    return g.sort_values("negative_mentions", ascending=False).reset_index(drop=True)


def search_reviews(rv: pd.DataFrame, query: str = "", themes: Optional[List[str]] = None, sentiments: Optional[List[str]] = None) -> pd.DataFrame:
    out = rv
    if query:
        out = out[out["review_text"].str.contains(re.escape(query), case=False, na=False)]
    if themes:
        out = out[out["theme_list"].map(lambda x: any(t in x for t in themes))]
    if sentiments:
        out = out[out["sentiment"].isin(sentiments)]
    return out
