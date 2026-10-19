import pandas as pd
import streamlit as st

from retailiq.analytics import reviews as RV
from retailiq.analytics.dataset import between
from retailiq.ui import cached, state
from retailiq.ui import charts as CH
from retailiq.ui import components as C
from retailiq.ui.formatting import pct

ds = state.get_dataset()
key = state.data_key(ds)

st.write("What do customers say about our products and service? Review text is **simulated from templates**: use this page to see how the analysis works, "
         "not to learn about real customers.")
rv_all = cached.reviews(ds, key)
if rv_all.empty:
    C.empty_state("There are no reviews in the database.")
    st.stop()
p = C.period_picker(ds, "rv", default="Last 12 months", compare=False)
with st.container(horizontal=True):
    stores = st.multiselect("Store", ds.store_ids, format_func=lambda s: ds.store_names[s], placeholder="All stores", key="rv_store")
    cats = st.multiselect("Category", sorted(rv_all["category"].unique()), placeholder="All categories", key="rv_cat")
    prods = st.multiselect("Product", list(ds.product_names), format_func=ds.product_label, placeholder="All products", key="rv_prod")
rv = rv_all[between(rv_all["review_date"], p["start"], p["end"]) & rv_all["store_id"].isin(stores or ds.store_ids) & rv_all["category"].isin(cats or rv_all["category"].unique())
            & rv_all["product_id"].isin(prods or list(ds.product_names))]
if rv.empty:
    C.empty_state("No reviews match these filters.")
    st.stop()
sm = RV.rating_summary(rv)
with st.container(horizontal=True):
    C.kpi("Reviews", f"{sm['count']:,}")
    C.kpi("Average rating", f"{sm['avg_rating']:.2f} / 5")
    C.kpi("Positive (4-5 stars)", pct((rv['rating'] >= 4).mean()))
    C.kpi("Negative (1-2 stars)", pct((rv['rating'] <= 2).mean()), delta_color="off")
    C.kpi("Text sentiment negative", pct((rv['sentiment'] == 'negative').mean()), help="From the rule-based text baseline, not the star rating.", delta_color="off")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Ratings", "Themes", "Products & trends", "Read the reviews", "Sentiment baseline"])
with tab1:
    a, b = st.columns(2)
    with a:
        dist = sm["distribution"].rename_axis("stars").reset_index(name="reviews")
        dist["stars"] = dist["stars"].astype(str) + " ★"
        CH.show(CH.bars(dist, "stars", {"Reviews": "reviews"}, "Rating distribution", money_axis=False, y_title="Reviews"), "Distribution of review ratings from 1 to 5 stars", key="rv_dist")
    with b:
        by = st.segmented_control("Trend by", ["overall", "category", "store"], default="overall", required=True, key="rv_by", format_func=str.capitalize)
        d = rv.assign(overall="All reviews", store=rv["store_name"])
        tr = d.groupby(["month", by], as_index=False).agg(reviews=("review_id", "count"), avg_rating=("rating", "mean"))
        piv = tr.pivot(index="month", columns=by, values="avg_rating").reset_index()
        series = {str(c): c for c in piv.columns if c != "month"}
        fig = CH.lines(piv, "month", series, "Average rating by month", money_axis=False, y_title="Stars")
        fig.update_yaxes(range=[1, 5])
        CH.show(fig, "Average monthly rating", key="rv_trend")
        C.note("info", "Months with few reviews swing widely; check the review counts in the table before reading a trend.")

with tab2:
    th = RV.theme_table(rv)
    if th.empty:
        st.write("No themes detected.")
    else:
        a, b = st.columns(2)
        with a:
            CH.show(CH.bars(th, "theme", {"Mentions": "mentions"}, "How often each theme is mentioned", money_axis=False, horizontal=True, height=300), "Mentions by theme", key="rv_theme")
        with b:
            C.table(th, pct_cols=["negative_share"], count_cols=["mentions"], dec_cols=["avg_rating"], labels={"negative_share": "Share negative", "avg_rating": "Average rating"}, name="review_themes", alt="Review themes", download=False)
        theme_pick = st.selectbox("See the reviews behind a theme", th["theme"], key="rv_theme_pick")
        ex = RV.search_reviews(rv, themes=[theme_pick]).sort_values("rating")
        C.table(ex.head(200), columns=["review_date", "product_name", "store_name", "rating", "sentiment", "review_text"], labels={"review_text": "Review"}, name=f"reviews_{theme_pick}",
                alt=f"Reviews mentioning {theme_pick}", height=320)
    C.definition("themes", "A review mentions a theme when one of its keywords appears (e.g. *price*: expensive, discount, value for money; *delivery*: delivered, courier, arrived; "
                           "*availability*: out of stock, unavailable; *service*: staff, billing, queue; *quality*: sturdy, flimsy, broke, leak). Keyword lists are in `retailiq/analytics/reviews.py`. "
                           "One review can mention several themes.")

with tab3:
    dec = RV.declining_products(ds, rv_all, ds.ref_date)
    st.markdown("**Falling ratings** - average rating in the last 90 days versus the 180 days before (needs ≥ 8 reviews in each window):")
    if dec.empty:
        st.write("Not enough reviews per product to compare.")
    else:
        C.table(dec, columns=["product_name", "prior_reviews", "recent_reviews", "prior_avg", "recent_avg", "change", "flag"], dec_cols=["prior_avg", "recent_avg", "change"], count_cols=["prior_reviews", "recent_reviews"],
                labels={"flag": "Flagged (drop ≥ 0.4)", "prior_avg": "Prior avg", "recent_avg": "Recent avg"}, name="declining_products", alt="Products with falling ratings")
    rep = RV.repeated_complaints(ds, rv_all, ds.ref_date)
    st.markdown("**Repeated complaints** - a product + theme with ≥ 5 negative reviews in the last 120 days that make up ≥ 25% of the product's reviews:")
    if rep.empty:
        st.success("No product has repeated complaints under this rule.", icon=":material/check_circle:")
    else:
        C.table(rep, columns=["product_name", "theme", "negative_mentions", "product_reviews", "share_of_product_reviews"], pct_cols=["share_of_product_reviews"], count_cols=["negative_mentions", "product_reviews"], name="repeated_complaints",
                alt="Repeated complaints by product and theme")
        worst = rep.iloc[0]
        st.warning(f"**{worst['product_name']}**: {worst['negative_mentions']} negative '{worst['theme']}' reviews in 120 days. Read them under *Read the reviews* before acting.", icon=":material/warning:")
    pr = rv.groupby(["product_id", "product_name"], as_index=False).agg(reviews=("review_id", "count"), avg_rating=("rating", "mean"), negative=("rating", lambda s: float((s <= 2).mean())))
    C.table(pr.sort_values("avg_rating"), count_cols=["reviews"], pct_cols=["negative"], dec_cols=["avg_rating"], labels={"negative": "Share 1-2 stars"}, name="rating_by_product", alt="Average rating by product")

with tab4:
    q = st.text_input("Search review text", placeholder="e.g. leaking, late, staff, price", key="rv_q")
    c1, c2 = st.columns(2)
    sent = c1.multiselect("Sentiment (text baseline)", ["positive", "neutral", "negative"], placeholder="Any", key="rv_sent")
    stars = c2.slider("Stars", 1, 5, (1, 5), key="rv_stars")
    ex = RV.search_reviews(rv, q, None, sent or None)
    ex = ex[ex["rating"].between(*stars)].sort_values("review_date", ascending=False)
    st.caption(f"{len(ex):,} reviews match" + (" (showing the latest 300; the download has all)" if len(ex) > 300 else ""))
    C.table(ex.head(300), columns=["review_id", "review_date", "product_name", "store_name", "rating", "sentiment", "themes", "review_text"], labels={"review_text": "Review", "sentiment": "Text sentiment"},
            name="review_search", alt="Searchable customer reviews", download=False, height=420)
    st.download_button("Download matching reviews (CSV)", ex.drop(columns=["theme_list"]).to_csv(index=False).encode("utf-8-sig"), "retailiq_reviews.csv", "text/csv", icon=":material/download:")

with tab5:
    ag = RV.baseline_agreement(rv)
    st.markdown("""**How the baseline works.** Positive and negative words from two short lists are counted (with simple negation: *not good* counts as negative).
Score = (positive − negative) ÷ total hits; above +0.2 is *positive*, below −0.2 *negative*, otherwise *neutral*. It is transparent and fast, and every score can be inspected below.

**Limits.** It does not understand sarcasm, context or words outside the lists, and can mislabel mixed reviews.""")
    st.warning(f"Agreement with star ratings is **{pct(ag['agreement'])}** on {ag['n']:,} reviews, but that is *not* the accuracy of the method: the review text was generated from templates, "
               "so the words match the ratings far more neatly than real reviews would. Do not quote this as model performance.", icon=":material/warning:")
    st.markdown("Text sentiment (columns) against star-rating sentiment (rows):")
    st.dataframe(ag["matrix"], alt="Confusion matrix of text sentiment against star rating sentiment")
    pick = st.selectbox("Inspect how one review was scored", rv["review_id"].head(500), key="rv_inspect")
    row = rv[rv["review_id"] == pick].iloc[0]
    st.write(f"*“{row['review_text']}”*")
    st.write(f"Stars: **{row['rating']}** · text sentiment: **{row['sentiment']}** (score {row['sentiment_score']:+.2f})")
    st.write(f"Positive words found: {row['positive_words'] or '-'} · Negative words found: {row['negative_words'] or '-'} · Themes: {row['themes'] or '-'}")
