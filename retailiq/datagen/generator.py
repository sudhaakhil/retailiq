"""Connected synthetic data generator (ALL OUTPUT IS SIMULATED).

How it works (plain-English summary)
------------------------------------
The generator runs a day-by-day simulation of a two-store retailer:

1. Each morning, purchase orders that arrive today are received into stock
   (a supplier invoice is raised and later paid according to supplier terms).
2. Customer returns that were scheduled for today are put back into stock and refunded.
3. Shoppers arrive. Their number follows store size, growth, month/weekday
   seasonality, Diwali and marketing campaigns. Some shoppers are registered
   loyalty customers (so repeat behaviour can be analysed), the rest are anonymous.
   Business customers occasionally place bulk orders on 30-day credit.
4. Every basket line is filled from stock. If stock is short, the line is cut
   (the customer leaves with less) and the stock-out is remembered for reviews.
   Cost of goods sold uses the store's weighted-average unit cost at that moment.
5. In the evening each product's stock position is compared with a reorder point;
   orders are grouped per supplier into purchase orders. Some suppliers deliver late.
6. End-of-day balances are recorded.

After the daily loop, operating expenses, marketing media, loans, owner equity,
budgets, reviews and a unified cash ledger are derived from the simulated events,
so every table reconciles with the others.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..analytics.accounting import add_months, amortization_schedule, depreciation_schedule, fixed_assets_net, month_end
from ..config import GENERATOR_VERSION, GeneratorConfig
from . import catalog as C

ID_SHARE = 0.22            # share of retail baskets made by registered customers
EXTRA_LINES_MEAN = 0.6     # basket lines = 1 + Poisson(0.6), capped at 4
B2B_ORDER_PROB = 1 / 30    # per business customer per trading day
B2B_DISCOUNT = 0.08
LOYALTY_DISCOUNT = 0.05
LOYALTY_DISCOUNT_PROB = 0.12
CLEARANCE = {"product_id": "P04", "start": dt.date(2026, 6, 1), "pct": 0.15}
REVIEW_PROB = 0.12
LATE_DELIVERY_PROB = {"S01": 0.10, "S02": 0.22}
SAFETY_Z = 1.65
DEMAND_DISPERSION = 1.5    # variance / mean of daily unit demand (over-dispersed Poisson)
COVER_DAYS = 50
PLANNING_EXTRA_DAYS = 160  # planning arrays extend beyond the last simulated day


def _iso(d) -> Optional[str]:
    if d is None or (isinstance(d, float) and math.isnan(d)):
        return None
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, pd.Timestamp):
        d = d.date()
    return d.isoformat()


class _Simulation:
    def __init__(self, cfg: GeneratorConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.start = cfg.start_date
        self.end = cfg.end_date
        self.n_days = (self.end - self.start).days + 1
        self.H = self.n_days + PLANNING_EXTRA_DAYS
        self.dates = [self.start + dt.timedelta(days=k) for k in range(self.H)]
        self.stores = [s["store_id"] for s in C.STORES]
        self.store_cfg = {s["store_id"]: s for s in C.STORES}
        self.products = C.PRODUCTS
        self.P = len(self.products)
        self.pids = [p["product_id"] for p in self.products]
        self.pidx = {pid: i for i, pid in enumerate(self.pids)}
        self.prod_cat = [p["category"] for p in self.products]
        self.suppliers = {s["supplier_id"]: s for s in C.SUPPLIERS}
        self.campaigns = [c for c in C.CAMPAIGNS]
        self._build_planning_arrays()

    # ------------------------------------------------------------------ demand model
    def _campaigns_on(self, store_id: str, d: dt.date) -> List[dict]:
        return [c for c in self.campaigns if c["start_date"] <= d <= c["end_date"]
                and (c["store_id"] is None or c["store_id"] == store_id)]

    def _build_planning_arrays(self) -> None:
        H, S, P = self.H, len(self.stores), self.P
        month = np.array([d.month for d in self.dates])
        dow = np.array([d.weekday() for d in self.dates])
        t_years = np.arange(H) / 365.0
        diwali = np.ones(H)
        for k, d in enumerate(self.dates):
            nearest = min(C.DIWALI.values(), key=lambda x: abs((x - d).days))
            delta = (d - nearest).days
            if abs(delta) <= 25:
                diwali[k] = 1 + 0.45 * math.exp(-(((delta + 4) / 7.0) ** 2))
        base_season = np.array([C.MONTH_SEASON[m] for m in month]) * diwali * np.array(C.WEEKDAY_FACTOR)[dow]

        self.traffic = np.zeros((S, H))
        self.traffic_plan = np.zeros((S, H))
        cat_uplift = np.zeros((S, P, H))
        for si, sid in enumerate(self.stores):
            sc = self.store_cfg[sid]
            camp_mult = np.ones(H)
            for k, d in enumerate(self.dates):
                for c in self._campaigns_on(sid, d):
                    camp_mult[k] *= 1 + c["traffic_uplift"]
                    if c["target_category"] and c["category_uplift"]:
                        for pi in range(P):
                            if self.prod_cat[pi] == c["target_category"]:
                                cat_uplift[si, pi, k] += c["category_uplift"]
            base = sc["base_txn"] * self.cfg.demand_scale * base_season * camp_mult
            self.traffic[si] = base * (1 + sc["growth"]) ** t_years
            self.traffic_plan[si] = base * (1 + sc["planned_growth"]) ** t_years

        # product weights
        cat_season = np.ones((P, H))
        trend_actual = np.ones((P, H))
        for pi, p in enumerate(self.products):
            cs = C.CATEGORY_SEASON.get(p["category"], {})
            ps = C.PRODUCT_SEASON.get(p["product_id"])
            cat_season[pi] = [(ps or cs).get(m, 1.0) for m in month]
        t = np.arange(H)
        trend_actual[self.pidx["P04"]] = np.maximum(0.3, 1 - 0.6 * t / 730.0)  # speaker demand fades
        trend_actual[self.pidx["P13"]] = 1 + 0.15 * t / 365.0
        trend_actual[self.pidx["P20"]] = 1 + 0.10 * t / 365.0
        defect_k = (C.DEFECT_START - self.start).days + 30
        if 0 <= defect_k < H:
            trend_actual[self.pidx[C.DEFECT_PRODUCT], defect_k:] *= 0.75  # word of mouth after the defect
        demand = np.array([p["demand"] for p in self.products])[:, None]
        self.weights = np.zeros((S, P, H))
        weights_plan = np.zeros((S, P, H))
        for si in range(S):
            self.weights[si] = demand * cat_season * trend_actual * (1 + cat_uplift[si])
            weights_plan[si] = demand * cat_season * (1 + cat_uplift[si])
        qty_mult = 1 + np.array([p["qty_extra"] for p in self.products])[:, None]
        e_lines = 1 + EXTRA_LINES_MEAN

        # Manager's planned units/day (retail + business). Deliberately ignores the speaker's
        # demand decline and the bottle defect, which creates realistic planning error.
        self.plan_units = np.zeros((S, P, H))
        trading = (dow != 6).astype(float)
        for si, sid in enumerate(self.stores):
            share = weights_plan[si] / weights_plan[si].sum(axis=0, keepdims=True)
            self.plan_units[si] = self.traffic_plan[si] * e_lines * share * qty_mult
            n_biz = self.cfg.n_business_customers / len(self.stores)
            per_prod = n_biz * B2B_ORDER_PROB * 2.0 / len(C.B2B_PRODUCTS) * 7.5
            for pid in C.B2B_PRODUCTS:
                self.plan_units[si, self.pidx[pid]] += per_prod * trading

    # ------------------------------------------------------------------ helpers
    def unit_cost_on(self, pid: str, d: dt.date) -> float:
        p = self.products[self.pidx[pid]]
        cost = float(p["initial_unit_cost"])
        for eff, pct in C.SUPPLIER_PRICE_CHANGES.get(p["supplier_id"], []):
            if d >= eff:
                cost *= 1 + pct
        return round(cost, 2)

    def _active_campaign(self, store_id: str, d: dt.date) -> Optional[dict]:
        camps = self._campaigns_on(store_id, d)
        return camps[0] if camps else None

    # ------------------------------------------------------------------ customers
    def _make_customers(self) -> pd.DataFrame:
        rng = self.rng
        cfg = self.cfg
        rows = []
        n_new = max(cfg.n_retail_customers - cfg.n_existing_customers, 0)
        total = cfg.n_existing_customers + n_new
        home = rng.choice(self.stores, size=total, p=[0.58, 0.42])
        reg_idx = np.zeros(total, dtype=int)
        # existing customers registered before the start date
        for i in range(cfg.n_existing_customers):
            earliest = dt.date(2023, 1, 1) if home[i] == "S01" else dt.date(2023, 4, 1)
            span = (self.start - earliest).days
            reg_idx[i] = -int(rng.integers(1, span))
        # new customers arrive in proportion to planned traffic (busier periods -> more sign-ups)
        for i in range(cfg.n_existing_customers, total):
            si = self.stores.index(home[i])
            w = self.traffic_plan[si, : self.n_days].copy()
            for c in self.campaigns:
                if c["objective"] == "acquisition" and (c["store_id"] in (None, home[i])):
                    a = max((c["start_date"] - self.start).days, 0)
                    b = min((c["end_date"] - self.start).days + 1, self.n_days)
                    if a < b:
                        w[a:b] *= 3.0
            reg_idx[i] = int(rng.choice(self.n_days, p=w / w.sum()))
        propensity = rng.lognormal(0.0, 0.8, size=total)
        # Customer lifetimes are a mixture: many try the store once or twice, others stay for years.
        short_lived = rng.random(total) < 0.45
        lifetime = np.where(short_lived, rng.exponential(20.0, size=total), rng.exponential(550.0, size=total)).astype(int) + 1
        existing_lapsed = rng.random(total) < 0.25
        existing_stay = np.where(existing_lapsed, rng.exponential(80.0, size=total), rng.exponential(600.0, size=total)).astype(int) + 1
        churn_idx = np.where(reg_idx < 0, existing_stay, reg_idx + lifetime)
        referral = rng.random(total) < 0.3
        for i in range(total):
            name = f"{rng.choice(C.FIRST_NAMES)} {rng.choice(C.LAST_NAMES)}"
            rows.append({
                "customer_id": f"C{i + 1:04d}", "customer_name": name, "customer_type": "retail",
                "home_store_id": home[i], "registration_date": self.start + dt.timedelta(days=int(reg_idx[i])),
                "acquisition_channel": "Referral" if referral[i] else "Walk-in", "acquisition_campaign_id": None,
                "credit_terms_days": 0, "x_reg_idx": int(reg_idx[i]), "x_churn_idx": int(churn_idx[i]),
                "x_propensity": float(propensity[i]), "x_late_profile": None,
            })
        nb = cfg.n_business_customers
        for j in range(nb):
            store = self.stores[j % len(self.stores)]
            existing = j < int(nb * 0.75)
            r_idx = -int(rng.integers(30, 500)) if existing else int(rng.integers(0, max(self.n_days - 60, 1)))
            prof = rng.choice(["prompt", "slow", "late"], p=[0.6, 0.3, 0.1])
            name = f"{C.BUSINESS_PREFIX[j % len(C.BUSINESS_PREFIX)]} {C.BUSINESS_SUFFIX[j % len(C.BUSINESS_SUFFIX)]} (sim)"
            rows.append({
                "customer_id": f"B{j + 1:03d}", "customer_name": name, "customer_type": "business",
                "home_store_id": store, "registration_date": self.start + dt.timedelta(days=r_idx),
                "acquisition_channel": "Business sales", "acquisition_campaign_id": None,
                "credit_terms_days": 30, "x_reg_idx": r_idx, "x_churn_idx": 10 ** 6,
                "x_propensity": 1.0, "x_late_profile": prof,
            })
        return pd.DataFrame(rows)

    def _lateness(self, profile: str) -> int:
        if profile == "prompt":
            return int(self.rng.integers(0, 4))
        if profile == "slow":
            return int(self.rng.integers(5, 16))
        return int(self.rng.integers(20, 46))

    # ------------------------------------------------------------------ main loop
    def run(self) -> Dict[str, pd.DataFrame]:
        rng = self.rng
        cust = self._make_customers()
        retail = cust[cust.customer_type == "retail"].reset_index(drop=True)
        business = cust[cust.customer_type == "business"].reset_index(drop=True)
        r_ids = retail.customer_id.to_numpy()
        r_store = retail.home_store_id.to_numpy()
        r_reg = retail.x_reg_idx.to_numpy()
        r_churn = retail.x_churn_idx.to_numpy()
        r_prop = retail.x_propensity.to_numpy()

        S = len(self.stores)
        qty = np.zeros((S, self.P), dtype=np.int64)
        value = np.zeros((S, self.P))
        avg_cost = np.array([[float(p["initial_unit_cost"]) for p in self.products]] * S)
        on_order = np.zeros((S, self.P), dtype=np.int64)
        last_stockout = np.full((S, self.P), -999)

        txns, lines, rets, receipts, movements = [], [], [], [], []
        pos_rows, pol_rows, inv_rows, pay_rows, daily_rows = [], [], [], [], []
        review_ctx = []
        receipts_due = defaultdict(list)   # day index -> list of PO dicts
        returns_due = defaultdict(list)
        counters = defaultdict(int)

        def nid(prefix: str, width: int) -> str:
            counters[prefix] += 1
            return f"{prefix}{counters[prefix]:0{width}d}"

        opening_date = self.start - dt.timedelta(days=1)
        # ---------------- opening stock
        for si, sid in enumerate(self.stores):
            for pi, p in enumerate(self.products):
                lt = self.suppliers[p["supplier_id"]]["lead_time_days"]
                horizon = 200 if p["product_id"] == "P04" else lt + 40
                units = self.plan_units[si, pi, :horizon].sum()
                units = int(math.ceil(units / p["case_pack"]) * p["case_pack"])
                val = round(units * float(p["initial_unit_cost"]), 2)
                qty[si, pi], value[si, pi] = units, val
                movements.append({"movement_id": nid("M", 8), "movement_date": opening_date, "store_id": sid,
                                  "product_id": p["product_id"], "movement_type": "opening", "quantity": units,
                                  "unit_cost": float(p["initial_unit_cost"]), "value": val, "reference_id": None})
        opening_inventory = float(value.sum())

        # ---------------- opening payables (recent purchases not yet paid)
        opening_ap = 0.0
        for sup_id, sup in self.suppliers.items():
            sup_val = sum(value[si, pi] for si in range(S) for pi, p in enumerate(self.products) if p["supplier_id"] == sup_id)
            amount = round(0.4 * sup_val, 2)
            inv_date = self.start - dt.timedelta(days=12)
            due = inv_date + dt.timedelta(days=sup["payment_terms_days"])
            inv_id = nid("SI", 7)
            inv_rows.append({"invoice_id": inv_id, "supplier_id": sup_id, "po_id": None, "invoice_date": inv_date,
                             "due_date": due, "amount": amount, "is_opening": 1})
            if due <= self.end:
                pay_rows.append({"payment_id": nid("SP", 7), "invoice_id": inv_id, "supplier_id": sup_id,
                                 "payment_date": due, "amount": amount})
            opening_ap += amount

        # ---------------- opening receivables (business invoices outstanding)
        opening_ar_rows = []
        for b in business.itertuples(index=False):
            if b.x_reg_idx >= 0:
                continue
            inv_date = self.start - dt.timedelta(days=int(rng.integers(1, 31)))
            due = inv_date + dt.timedelta(days=30)
            amount = round(float(rng.uniform(2500, 6500)), 2)
            orid = nid("OR", 4)
            opening_ar_rows.append({"opening_receivable_id": orid, "customer_id": b.customer_id, "store_id": b.home_store_id,
                                    "invoice_date": inv_date, "due_date": due, "amount": amount})
            pay_date = due + dt.timedelta(days=self._lateness(b.x_late_profile))
            if pay_date <= self.end:
                receipts.append({"receipt_id": nid("R", 7), "receipt_date": pay_date, "transaction_id": None,
                                 "opening_receivable_id": orid, "customer_id": b.customer_id, "store_id": b.home_store_id,
                                 "payment_method": "credit", "amount": amount})

        price = np.array([float(p["unit_price"]) for p in self.products])
        qty_extra = np.array([p["qty_extra"] for p in self.products])
        b2b_idx = np.array([self.pidx[p] for p in C.B2B_PRODUCTS])

        for t in range(self.n_days):
            d = self.dates[t]
            dow = d.weekday()
            for si, sid in enumerate(self.stores):
                # 1) receive purchase orders due today
                for po in receipts_due.pop((t, si), []):
                    sup = self.suppliers[po["supplier_id"]]
                    for ln in po["_lines"]:
                        pi = self.pidx[ln["product_id"]]
                        qty[si, pi] += ln["quantity_ordered"]
                        value[si, pi] = round(value[si, pi] + ln["line_cost"], 2)
                        on_order[si, pi] -= ln["quantity_ordered"]
                        avg_cost[si, pi] = value[si, pi] / qty[si, pi]
                        movements.append({"movement_id": nid("M", 8), "movement_date": d, "store_id": sid,
                                          "product_id": ln["product_id"], "movement_type": "receipt",
                                          "quantity": ln["quantity_ordered"], "unit_cost": ln["unit_cost"],
                                          "value": ln["line_cost"], "reference_id": ln["po_line_id"]})
                    inv_id = nid("SI", 7)
                    due = d + dt.timedelta(days=sup["payment_terms_days"])
                    inv_rows.append({"invoice_id": inv_id, "supplier_id": po["supplier_id"], "po_id": po["po_id"],
                                     "invoice_date": d, "due_date": due, "amount": po["total_cost"], "is_opening": 0})
                    pay_date = due if rng.random() < 0.85 else due + dt.timedelta(days=int(rng.integers(1, 8)))
                    if pay_date <= self.end:
                        pay_rows.append({"payment_id": nid("SP", 7), "invoice_id": inv_id, "supplier_id": po["supplier_id"],
                                         "payment_date": pay_date, "amount": po["total_cost"]})

                # 2) process returns due today
                for r in returns_due.pop((t, si), []):
                    pi = self.pidx[r["product_id"]]
                    qty[si, pi] += r["quantity"]
                    value[si, pi] = round(value[si, pi] + r["cogs_reversal"], 2)
                    avg_cost[si, pi] = value[si, pi] / qty[si, pi]
                    movements.append({"movement_id": nid("M", 8), "movement_date": d, "store_id": sid,
                                      "product_id": r["product_id"], "movement_type": "return", "quantity": r["quantity"],
                                      "unit_cost": r["unit_cost"], "value": r["cogs_reversal"], "reference_id": r["return_id"]})
                    rets.append({k: v for k, v in r.items() if k != "unit_cost"})

                # 3) shoppers
                lam = self.traffic[si, t]
                n_ident = rng.poisson(lam * ID_SHARE)
                n_anon = rng.poisson(lam * (1 - ID_SHARE))
                forced = np.where((r_store == sid) & (r_reg == t))[0]
                pool = np.where((r_store == sid) & (r_reg < t) & (r_churn > t))[0]
                k_other = min(max(n_ident - len(forced), 0), len(pool))
                chosen = rng.choice(pool, size=k_other, replace=False, p=r_prop[pool] / r_prop[pool].sum()) if k_other else np.array([], dtype=int)
                baskets = [("retail", int(i)) for i in forced] + [("retail", int(i)) for i in chosen] + [("anon", -1)] * n_anon
                if dow != 6:
                    for bi, b in enumerate(business.itertuples(index=False)):
                        if b.home_store_id == sid and b.x_reg_idx <= t and rng.random() < B2B_ORDER_PROB:
                            baskets.append(("business", bi))
                order = rng.permutation(len(baskets))
                camp = self._active_campaign(sid, d)
                w = self.weights[si, :, t]
                w = w / w.sum()

                for oi in order:
                    kind, ci = baskets[oi]
                    if kind == "business":
                        n_lines = int(rng.integers(1, 4))
                        picks = rng.choice(b2b_idx, size=n_lines, replace=False)
                        wanted = [(int(pi), int(rng.integers(3, 13))) for pi in picks]
                        customer_id = business.customer_id[ci]
                        channel, method = "business", "credit"
                    else:
                        n_lines = 1 + min(int(rng.poisson(EXTRA_LINES_MEAN)), 3)
                        picks = rng.choice(self.P, size=n_lines, replace=False, p=w)
                        wanted = [(int(pi), 1 + int(rng.poisson(qty_extra[pi]))) for pi in picks]
                        customer_id = r_ids[ci] if kind == "retail" else None
                        online = rng.random() < self.store_cfg[sid]["online_share"]
                        channel = "online" if online else "in_store"
                        if online:
                            method = "card" if rng.random() < 0.55 else "upi"
                        else:
                            u = rng.random()
                            method = "cash" if u < 0.25 else ("upi" if u < 0.65 else "card")

                    # campaign attribution: the campaign code is redeemed by a share of eligible baskets
                    campaign_id, camp_pct, camp_cat = None, 0.0, None
                    if camp is not None and kind != "business":
                        eligible = camp["target_category"] is None or any(self.prod_cat[pi] == camp["target_category"] for pi, _ in wanted)
                        if eligible and rng.random() < camp["redemption"]:
                            campaign_id, camp_pct, camp_cat = camp["campaign_id"], camp["discount_pct"], camp["target_category"]
                    loyalty = kind == "retail" and campaign_id is None and rng.random() < LOYALTY_DISCOUNT_PROB

                    txn_id = f"T{counters['T'] + 1:07d}"
                    txn_lines, shortfall = [], False
                    for pi, want in wanted:
                        have = int(qty[si, pi])
                        q = min(want, have)
                        if q < want:
                            shortfall = True
                            last_stockout[si, pi] = t
                        if q <= 0:
                            continue
                        pid = self.pids[pi]
                        if kind == "business":
                            pct = B2B_DISCOUNT
                        elif campaign_id and (camp_cat is None or self.prod_cat[pi] == camp_cat):
                            pct = camp_pct
                        elif loyalty:
                            pct = LOYALTY_DISCOUNT
                        else:
                            pct = 0.0
                        if pid == CLEARANCE["product_id"] and d >= CLEARANCE["start"] and kind != "business":
                            pct = max(pct, CLEARANCE["pct"])
                        gross = round(q * price[pi], 2)
                        disc = round(gross * pct, 2)
                        net = round(gross - disc, 2)
                        if q == have:
                            cogs = round(value[si, pi], 2)
                        else:
                            cogs = round(q * avg_cost[si, pi], 2)
                        unit_cost = round(cogs / q, 4)
                        qty[si, pi] -= q
                        value[si, pi] = round(value[si, pi] - cogs, 2)
                        if qty[si, pi] == 0:
                            value[si, pi] = 0.0
                        line_id = nid("L", 8)
                        txn_lines.append({"line_id": line_id, "transaction_id": txn_id, "product_id": pid, "quantity": q,
                                          "unit_price": price[pi], "discount_amount": disc, "net_amount": net,
                                          "unit_cost": unit_cost, "cogs_amount": cogs, "_gross": gross, "_pct": pct})
                        movements.append({"movement_id": nid("M", 8), "movement_date": d, "store_id": sid, "product_id": pid,
                                          "movement_type": "sale", "quantity": -q, "unit_cost": unit_cost, "value": -cogs,
                                          "reference_id": line_id})
                    if not txn_lines:
                        continue
                    counters["T"] += 1
                    gross_t = round(sum(x["_gross"] for x in txn_lines), 2)
                    disc_t = round(sum(x["discount_amount"] for x in txn_lines), 2)
                    net_t = round(sum(x["net_amount"] for x in txn_lines), 2)
                    if method == "credit":
                        due = d + dt.timedelta(days=30)
                        pay_date = due + dt.timedelta(days=self._lateness(business.x_late_profile[ci]))
                    else:
                        lag = {"cash": 0, "upi": 1, "card": 2}[method]
                        due = pay_date = d + dt.timedelta(days=lag)
                    txns.append({"transaction_id": txn_id, "transaction_date": d, "store_id": sid, "customer_id": customer_id,
                                 "channel": channel, "payment_method": method, "campaign_id": campaign_id,
                                 "gross_amount": gross_t, "discount_amount": disc_t, "net_amount": net_t, "due_date": due})
                    if pay_date <= self.end:
                        receipts.append({"receipt_id": nid("R", 7), "receipt_date": pay_date, "transaction_id": txn_id,
                                         "opening_receivable_id": None, "customer_id": customer_id, "store_id": sid,
                                         "payment_method": method, "amount": net_t})
                    late_delivery = channel == "online" and rng.random() < LATE_DELIVERY_PROB[sid]
                    for ln in txn_lines:
                        pi = self.pidx[ln["product_id"]]
                        lines.append({k: v for k, v in ln.items() if not k.startswith("_")})
                        if kind == "business":
                            continue
                        rr = self.products[pi]["return_rate"]
                        if ln["product_id"] == C.DEFECT_PRODUCT and d >= C.DEFECT_START:
                            rr = 0.08
                        if rng.random() < rr:
                            rd = d + dt.timedelta(days=int(rng.integers(1, 16)))
                            if rd <= self.end:
                                cat = self.prod_cat[pi]
                                reason = ("Defective" if ln["product_id"] == C.DEFECT_PRODUCT and d >= C.DEFECT_START
                                          else str(rng.choice(C.RETURN_REASONS.get(cat, C.RETURN_REASONS["default"]))))
                                returns_due[((rd - self.start).days, si)].append({
                                    "return_id": nid("RT", 6), "line_id": ln["line_id"], "transaction_id": txn_id,
                                    "return_date": rd, "store_id": sid, "product_id": ln["product_id"],
                                    "quantity": ln["quantity"], "refund_amount": ln["net_amount"],
                                    "cogs_reversal": ln["cogs_amount"], "reason": reason, "unit_cost": ln["unit_cost"]})
                        if kind == "retail" and rng.random() < REVIEW_PROB:
                            review_ctx.append({
                                "transaction_id": txn_id, "customer_id": customer_id, "product_id": ln["product_id"],
                                "store_id": sid, "date": d, "online": channel == "online", "late_delivery": late_delivery,
                                "availability_issue": shortfall or (t - last_stockout[si, pi] <= 7),
                                "discount_pct": ln["_pct"]})

                # 4) evening reorder check (continuous review, reorder point + order-up-to cover)
                todays_orders = defaultdict(list)
                for pi, p in enumerate(self.products):
                    sup = self.suppliers[p["supplier_id"]]
                    lt = sup["lead_time_days"]
                    fut = self.plan_units[si, pi, t + 1: t + 1 + lt]
                    mean_daily = float(fut.mean()) if len(fut) else 0.0
                    safety = SAFETY_Z * math.sqrt(lt) * math.sqrt(DEMAND_DISPERSION * max(mean_daily, 0.01))
                    rop = float(fut.sum()) + safety
                    position = int(qty[si, pi] + on_order[si, pi])
                    if position <= rop:
                        target = float(self.plan_units[si, pi, t + 1: t + 1 + lt + COVER_DAYS].sum()) + safety
                        cp = p["case_pack"]
                        q = int(math.ceil(max(target - position, cp) / cp) * cp)
                        q = min(q, (p["max_units_per_order"] // cp) * cp)
                        if q > 0:
                            todays_orders[p["supplier_id"]].append((pi, q))
                for sup_id, items in todays_orders.items():
                    sup = self.suppliers[sup_id]
                    po_id = nid("PO", 6)
                    nominal = sup["lead_time_days"]
                    if rng.random() < sup["on_time_rate"]:
                        actual = max(1, nominal + int(rng.integers(-1, 1)))
                    else:
                        actual = nominal + int(rng.integers(3, 13))
                    received_t = t + actual
                    po_lines = []
                    for pi, q in items:
                        uc = self.unit_cost_on(self.pids[pi], d)
                        po_lines.append({"po_line_id": nid("POL", 7), "po_id": po_id, "product_id": self.pids[pi],
                                         "quantity_ordered": q, "quantity_received": q if received_t < self.n_days else 0,
                                         "unit_cost": uc, "line_cost": round(q * uc, 2)})
                        on_order[si, pi] += q
                    total = round(sum(x["line_cost"] for x in po_lines), 2)
                    po = {"po_id": po_id, "supplier_id": sup_id, "store_id": sid, "order_date": d,
                          "expected_date": d + dt.timedelta(days=nominal),
                          "received_date": self.dates[received_t] if received_t < self.n_days else None,
                          "status": "received" if received_t < self.n_days else "open", "total_cost": total}
                    pos_rows.append(po)
                    pol_rows.extend(po_lines)
                    if received_t < self.n_days:
                        receipts_due[(received_t, si)].append({**po, "_lines": po_lines})

                # 5) end-of-day balances
                for pi in range(self.P):
                    q_ = int(qty[si, pi])
                    daily_rows.append((d, sid, self.pids[pi], q_, round(float(value[si, pi]), 2),
                                       round(float(value[si, pi]) / q_, 4) if q_ > 0 else round(float(avg_cost[si, pi]), 4),
                                       int(on_order[si, pi])))

        self.cust = cust
        self.opening = {"inventory": round(opening_inventory, 2), "ap": round(opening_ap, 2),
                        "ar": round(sum(r["amount"] for r in opening_ar_rows), 2)}
        out = {
            "sales_transactions": pd.DataFrame(txns),
            "sales_lines": pd.DataFrame(lines),
            "returns": pd.DataFrame(rets, columns=["return_id", "line_id", "transaction_id", "return_date", "store_id",
                                                    "product_id", "quantity", "refund_amount", "cogs_reversal", "reason"]),
            "customer_receipts": pd.DataFrame(receipts),
            "opening_receivables": pd.DataFrame(opening_ar_rows, columns=["opening_receivable_id", "customer_id", "store_id",
                                                                          "invoice_date", "due_date", "amount"]),
            "purchase_orders": pd.DataFrame(pos_rows),
            "purchase_order_lines": pd.DataFrame(pol_rows),
            "supplier_invoices": pd.DataFrame(inv_rows),
            "supplier_payments": pd.DataFrame(pay_rows),
            "stock_movements": pd.DataFrame(movements),
            "inventory_daily": pd.DataFrame(daily_rows, columns=["balance_date", "store_id", "product_id", "quantity_on_hand",
                                                                 "inventory_value", "avg_unit_cost", "quantity_on_order"]),
        }
        self.review_ctx = review_ctx
        return out

    # ------------------------------------------------------------------ marketing media
    def campaign_tables(self) -> Dict[str, pd.DataFrame]:
        rng = np.random.default_rng(self.cfg.seed + 2)
        camp_rows, daily = [], []
        for c in self.campaigns:
            if c["end_date"] < self.start:
                continue
            status = "completed" if c["end_date"] <= self.end else ("active" if c["start_date"] <= self.end else "planned")
            camp_rows.append({k: c[k] for k in ["campaign_id", "campaign_name", "channel", "objective", "start_date", "end_date",
                                                 "store_id", "target_category", "discount_pct", "planned_budget"]} | {"status": status})
            if status == "planned":
                continue
            n = (c["end_date"] - c["start_date"]).days + 1
            media = C.CHANNEL_MEDIA[c["channel"]]
            for k in range(n):
                d = c["start_date"] + dt.timedelta(days=k)
                if d > self.end or d < self.start:
                    continue
                spend = round(c["planned_budget"] / n * float(rng.lognormal(0, 0.15)), 2)
                impressions = int(spend / media["cpm"] * 1000 * float(rng.lognormal(0, 0.1)))
                clicks = int(rng.binomial(max(impressions, 0), media["ctr"]))
                daily.append({"campaign_id": c["campaign_id"], "activity_date": d, "spend": spend,
                              "impressions": impressions, "clicks": clicks})
        return {"campaigns": pd.DataFrame(camp_rows), "campaign_daily": pd.DataFrame(daily)}

    # ------------------------------------------------------------------ operating expenses
    def operating_expenses(self, txns: pd.DataFrame, camp_daily: pd.DataFrame, campaigns: pd.DataFrame) -> pd.DataFrame:
        rng = np.random.default_rng(self.cfg.seed + 3)
        rows = []
        counter = [0]

        def add(d, store, cat, behaviour, desc, amount, campaign_id=None):
            counter[0] += 1
            rows.append({"expense_id": f"EX{counter[0]:06d}", "expense_date": d, "store_id": store, "category": cat,
                         "cost_behaviour": behaviour, "campaign_id": campaign_id, "description": desc,
                         "amount": round(float(amount), 2)})

        def schedule_value(sched, d):
            val = sched[0][1]
            for eff, v in sched:
                if d >= eff:
                    val = v
            return val

        tx = txns.copy()
        tx["month"] = pd.to_datetime(tx["transaction_date"]).dt.to_period("M")
        m = self.start.replace(day=1)
        while m <= self.end:
            me = month_end(m)
            for sid in self.stores:
                add(m, sid, "Rent", "fixed", f"Store rent {m:%b %Y}", schedule_value(C.RENT[sid], m))
                add(me, sid, "Salaries & wages", "fixed", f"Store payroll {m:%b %Y}", schedule_value(C.SALARIES[sid], m))
                util = C.UTILITIES_BASE[sid] * (1.35 if m.month in (4, 5, 6) else 1.0) * float(rng.lognormal(0, 0.06))
                add(m.replace(day=10), sid, "Utilities", "fixed", f"Electricity & water {m:%b %Y}", util)
                add(m.replace(day=20), sid, "Repairs & maintenance", "fixed", f"Store maintenance {m:%b %Y}", rng.uniform(4000, 12000))
                mt = tx[(tx.store_id == sid) & (tx.month == pd.Period(m, "M"))]
                card = mt.loc[mt.payment_method == "card", "net_amount"].sum()
                add(me, sid, "Payment processing fees", "variable", f"Card acquiring fees {m:%b %Y} (1.8% of card sales)", card * C.CARD_FEE_RATE)
                n_online = int((mt.channel == "online").sum())
                add(me, sid, "Delivery & logistics", "variable", f"Home delivery {n_online} orders x {C.DELIVERY_COST_PER_ORDER:.0f}", n_online * C.DELIVERY_COST_PER_ORDER)
                add(me, sid, "Packaging & consumables", "variable", f"Bags and packaging {m:%b %Y} (0.6% of net sales)", mt["net_amount"].sum() * C.PACKAGING_RATE)
            add(me, None, "Salaries & wages", "fixed", f"Head-office payroll {m:%b %Y}", schedule_value(C.SALARIES[None], m))
            for cat, amount, day in C.HQ_FIXED:
                amt = amount * (float(rng.lognormal(0, 0.08)) if cat == "Office & administration" else 1.0)
                add(m.replace(day=day), None, cat, "fixed", f"{cat} {m:%b %Y}", amt)
            m = add_months(m, 1, 1)

        # marketing media is billed at month end per campaign
        if not camp_daily.empty:
            cd = camp_daily.copy()
            cd["me"] = pd.to_datetime(cd["activity_date"]).map(lambda x: month_end(x.date()))
            store_of = campaigns.set_index("campaign_id")["store_id"].to_dict()
            for (cid, me), grp in cd.groupby(["campaign_id", "me"], sort=True):
                add(me, store_of.get(cid), "Marketing", "discretionary", f"Media spend {cid} {me:%b %Y}", grp["spend"].sum(), cid)
        out = pd.DataFrame(rows)
        out = out.sort_values(["expense_date", "expense_id"]).reset_index(drop=True)
        return out

    # ------------------------------------------------------------------ reviews
    def reviews(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.cfg.seed + 1)
        rows = []
        for ctx in self.review_ctx:
            rdate = ctx["date"] + dt.timedelta(days=int(rng.integers(2, 22)))
            if rdate > self.end:
                continue
            p = self.products[self.pidx[ctx["product_id"]]]
            base = p["quality"]
            defect = ctx["product_id"] == C.DEFECT_PRODUCT and ctx["date"] >= C.DEFECT_START
            if defect:
                base = 2.4
            svc_issue = (ctx["store_id"] == C.SERVICE_ISSUE["store_id"] and C.SERVICE_ISSUE["start"] <= ctx["date"] <= C.SERVICE_ISSUE["end"]
                         and rng.random() < 0.45)
            score = base + rng.normal(0, 0.7)
            issues = []
            if defect:
                issues.append("defect")
            if ctx["late_delivery"]:
                score -= 1.5
                issues.append("delivery")
            if ctx["availability_issue"] and rng.random() < 0.6:
                score -= 0.8
                issues.append("availability")
            if svc_issue:
                score -= 1.2
                issues.append("service")
            if ctx["discount_pct"] >= 0.10:
                score += 0.3
            rating = int(min(5, max(1, round(score))))
            polarity = "pos" if rating >= 4 else ("neg" if rating <= 2 else "neu")

            themes = []
            if polarity != "pos" and issues and rng.random() < 0.85:
                themes.append(issues[0] if issues[0] != "defect" else "defect")
            else:
                opts = ["quality", "price", "service", "availability"] + (["delivery"] if ctx["online"] else [])
                probs = np.array([0.45, 0.25, 0.15, 0.05] + ([0.10] if ctx["online"] else []))
                themes.append(str(rng.choice(opts, p=probs / probs.sum())))
            if rng.random() < 0.35:
                second = str(rng.choice(["quality", "price", "service"]))
                if second not in themes:
                    themes.append(second)
            parts = []
            for i, th in enumerate(themes):
                pol = polarity
                if i > 0 and polarity == "neu":
                    pol = str(rng.choice(["pos", "neg"]))  # mixed review: one good point, one bad point
                if th == "defect":
                    pol = "neg"
                key = (th, pol) if (th, pol) in C.REVIEW_PHRASES else (th, "neu")
                parts.append(str(rng.choice(C.REVIEW_PHRASES[key])))
            if rng.random() < 0.3:
                parts.insert(0, f"Bought the {p['product_name'].lower()}.")
            closer = str(rng.choice(C.REVIEW_CLOSERS[polarity]))
            text = " ".join(x for x in parts + [closer] if x)
            rows.append({"review_date": rdate, "customer_id": ctx["customer_id"], "product_id": ctx["product_id"],
                         "store_id": ctx["store_id"], "transaction_id": ctx["transaction_id"], "rating": rating,
                         "review_text": text})
        df = pd.DataFrame(rows, columns=["review_date", "customer_id", "product_id", "store_id", "transaction_id", "rating", "review_text"])
        df = df.sort_values(["review_date", "transaction_id", "product_id"], kind="mergesort").reset_index(drop=True)
        df.insert(0, "review_id", [f"RV{i + 1:05d}" for i in range(len(df))])
        return df

    # ------------------------------------------------------------------ financing and assets
    def financing(self) -> Dict[str, pd.DataFrame]:
        loans_rows, pay_rows = [], []
        opening_loan = 0.0
        for ln in C.LOANS:
            if ln["start_date"] > self.end:
                continue
            sched = amortization_schedule(ln["principal"], ln["annual_rate"], ln["term_months"], ln["start_date"], ln["payment_day"])
            loans_rows.append({k: ln[k] for k in ["loan_id", "lender", "start_date", "principal", "annual_rate", "term_months", "payment_day"]})
            if ln["start_date"] < self.start:
                paid_before = sched.loc[sched.payment_date < pd.Timestamp(self.start), "principal"].sum()
                opening_loan += round(ln["principal"] - paid_before, 2)
            in_period = sched[(sched.payment_date >= pd.Timestamp(self.start)) & (sched.payment_date <= pd.Timestamp(self.end))]
            for r in in_period.itertuples(index=False):
                pay_rows.append({"loan_payment_id": f"LP{len(pay_rows) + 1:05d}", "loan_id": ln["loan_id"],
                                 "payment_date": r.payment_date.date(), "instalment_no": r.instalment_no,
                                 "interest_amount": r.interest, "principal_amount": r.principal, "balance_after": r.closing_balance})
        eq = [e for e in C.EQUITY if self.start <= e["txn_date"] <= self.end]
        m = self.start.replace(day=1)
        while m <= self.end:
            if m.month in (3, 6, 9, 12):
                eq.append({"equity_txn_id": None, "txn_date": m.replace(day=28), "txn_type": "owner_drawing",
                           "amount": C.OWNER_DRAWING_QUARTERLY, "description": f"Quarterly owner drawing {m:%b %Y}"})
            m = add_months(m, 1, 1)
        eq = sorted(eq, key=lambda e: e["txn_date"])
        for i, e in enumerate(eq):
            e["equity_txn_id"] = f"E{i + 1:02d}"
        capex = pd.DataFrame([c for c in C.CAPEX if c["capex_date"] <= self.end])
        return {
            "loans": pd.DataFrame(loans_rows),
            "loan_payments": pd.DataFrame(pay_rows, columns=["loan_payment_id", "loan_id", "payment_date", "instalment_no",
                                                             "interest_amount", "principal_amount", "balance_after"]),
            "equity_transactions": pd.DataFrame(eq, columns=["equity_txn_id", "txn_date", "txn_type", "amount", "description"]),
            "capital_expenditures": capex,
            "_opening_loan": opening_loan,
        }

    # ------------------------------------------------------------------ budgets
    def budgets(self, capex: pd.DataFrame, campaigns: pd.DataFrame) -> pd.DataFrame:
        rows = []
        dep = depreciation_schedule(capex, self.end)
        price = np.array([float(p["unit_price"]) for p in self.products])
        m = self.start.replace(day=1)
        while m <= self.end:
            a = (m - self.start).days
            b = (month_end(m) - self.start).days + 1
            costs = np.array([self.unit_cost_on(pid, m) for pid in self.pids])
            mkt_total = defaultdict(float)
            for c in campaigns.itertuples(index=False):
                cs, ce = c.start_date, c.end_date
                ndays = (ce - cs).days + 1
                overlap = (min(ce, month_end(m)) - max(cs, m)).days + 1
                if overlap > 0:
                    mkt_total[c.store_id or "HQ"] += c.planned_budget * overlap / ndays
            for si, sid in enumerate(self.stores):
                units = self.plan_units[si, :, a:b].sum(axis=1)
                ns = float((units * price).sum()) * (1 - 0.045) * 1.04
                cogs = float((units * costs).sum()) * 1.04
                sc = self.store_cfg[sid]
                rent = [v for eff, v in C.RENT[sid] if eff <= m][-1]
                sal = [v for eff, v in C.SALARIES[sid] if eff <= m][-1]
                util = C.UTILITIES_BASE[sid] * (1.3 if m.month in (4, 5, 6) else 1.0)
                variable = ns * (C.PACKAGING_RATE + 0.007) + self.traffic_plan[si, a:b].sum() * sc["online_share"] * C.DELIVERY_COST_PER_ORDER
                opex = rent + sal + util + 8000 + variable + mkt_total.get(sid, 0.0)
                d_store = dep[(dep.store_id == sid) & (dep.dep_date == pd.Timestamp(month_end(m)))]["amount"].sum()
                rows += [(m, sid, "net_sales", ns), (m, sid, "cogs", cogs), (m, sid, "operating_expenses", opex),
                         (m, sid, "depreciation", d_store)]
            hq_opex = [v for eff, v in C.SALARIES[None] if eff <= m][-1] + sum(x[1] for x in C.HQ_FIXED) + mkt_total.get("HQ", 0.0)
            d_hq = dep[(dep.store_id.isna()) & (dep.dep_date == pd.Timestamp(month_end(m)))]["amount"].sum()
            rows += [(m, "HQ", "operating_expenses", hq_opex), (m, "HQ", "depreciation", d_hq)]
            m = add_months(m, 1, 1)
        df = pd.DataFrame(rows, columns=["month", "store_id", "line_item", "amount"])
        df["amount"] = df["amount"].round(2)
        return df


def _cash_ledger(t: Dict[str, pd.DataFrame], capex_in_period: pd.DataFrame, loans: pd.DataFrame, start: dt.date) -> pd.DataFrame:
    parts = []
    r = t["customer_receipts"]
    parts.append(pd.DataFrame({"txn_date": r.receipt_date, "activity": "operating", "category": "Customer collections",
                               "amount": r.amount, "source_table": "customer_receipts", "source_id": r.receipt_id, "store_id": r.store_id}))
    rt = t["returns"]
    parts.append(pd.DataFrame({"txn_date": rt.return_date, "activity": "operating", "category": "Customer refunds",
                               "amount": -rt.refund_amount, "source_table": "returns", "source_id": rt.return_id, "store_id": rt.store_id}))
    sp = t["supplier_payments"]
    parts.append(pd.DataFrame({"txn_date": sp.payment_date, "activity": "operating", "category": "Supplier payments",
                               "amount": -sp.amount, "source_table": "supplier_payments", "source_id": sp.payment_id, "store_id": None}))
    ox = t["operating_expenses"]
    parts.append(pd.DataFrame({"txn_date": ox.expense_date, "activity": "operating", "category": "Operating expenses: " + ox.category,
                               "amount": -ox.amount, "source_table": "operating_expenses", "source_id": ox.expense_id, "store_id": ox.store_id}))
    if not capex_in_period.empty:
        parts.append(pd.DataFrame({"txn_date": capex_in_period.capex_date, "activity": "investing", "category": "Capital expenditure",
                                   "amount": -capex_in_period.amount, "source_table": "capital_expenditures",
                                   "source_id": capex_in_period.capex_id, "store_id": capex_in_period.store_id}))
    drawn = loans[pd.to_datetime(loans.start_date) >= start] if not loans.empty else loans
    if not drawn.empty:
        parts.append(pd.DataFrame({"txn_date": drawn.start_date, "activity": "financing", "category": "Loan drawdown",
                                   "amount": drawn.principal, "source_table": "loans", "source_id": drawn.loan_id, "store_id": None}))
    lp = t["loan_payments"]
    if not lp.empty:
        parts.append(pd.DataFrame({"txn_date": lp.payment_date, "activity": "financing", "category": "Loan interest",
                                   "amount": -lp.interest_amount, "source_table": "loan_payments",
                                   "source_id": lp.loan_payment_id + "-I", "store_id": None}))
        parts.append(pd.DataFrame({"txn_date": lp.payment_date, "activity": "financing", "category": "Loan principal repayment",
                                   "amount": -lp.principal_amount, "source_table": "loan_payments",
                                   "source_id": lp.loan_payment_id + "-P", "store_id": None}))
    eq = t["equity_transactions"]
    if not eq.empty:
        sign = np.where(eq.txn_type == "owner_investment", 1.0, -1.0)
        parts.append(pd.DataFrame({"txn_date": eq.txn_date, "activity": "financing",
                                   "category": np.where(eq.txn_type == "owner_investment", "Owner investment", "Owner drawings"),
                                   "amount": eq.amount * sign, "source_table": "equity_transactions",
                                   "source_id": eq.equity_txn_id, "store_id": None}))
    led = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    led["txn_date"] = pd.to_datetime(led["txn_date"])
    led = led.sort_values(["txn_date", "source_table", "source_id"], kind="mergesort").reset_index(drop=True)
    led["amount"] = led["amount"].round(2)
    led.insert(0, "cash_txn_id", [f"CL{i + 1:07d}" for i in range(len(led))])
    return led


def generate_dataset(cfg: Optional[GeneratorConfig] = None) -> Dict[str, pd.DataFrame]:
    """Run the simulation and return every table as a DataFrame (dates as ISO strings)."""
    cfg = cfg or GeneratorConfig()
    sim = _Simulation(cfg)
    t = sim.run()
    camp = sim.campaign_tables()
    t.update(camp)
    fin = sim.financing()
    opening_loan = fin.pop("_opening_loan")
    t.update(fin)
    t["operating_expenses"] = sim.operating_expenses(t["sales_transactions"], t["campaign_daily"], t["campaigns"])
    t["reviews"] = sim.reviews()
    t["budgets"] = sim.budgets(t["capital_expenditures"], t["campaigns"])

    # Derive acquisition attribution from each customer's first transaction (consistent by construction).
    cust = sim.cust.copy()
    tx = t["sales_transactions"]
    first = tx.dropna(subset=["customer_id"]).sort_values(["transaction_date", "transaction_id"]).groupby("customer_id").head(1)
    first = first.set_index("customer_id")
    camp_channel = t["campaigns"].set_index("campaign_id")["channel"].to_dict()
    for i, row in cust.iterrows():
        if row.customer_type != "retail" or row.customer_id not in first.index or row.x_reg_idx < 0:
            continue
        f = first.loc[row.customer_id]
        if f.campaign_id is not None and not pd.isna(f.campaign_id) and f.transaction_date == row.registration_date:
            cust.at[i, "acquisition_campaign_id"] = f.campaign_id
            cust.at[i, "acquisition_channel"] = camp_channel[f.campaign_id]
    t["customers"] = cust[[c for c in cust.columns if not c.startswith("x_")]]

    capex = t["capital_expenditures"]
    opening_date = cfg.start_date - dt.timedelta(days=1)
    fa_net = fixed_assets_net(capex[pd.to_datetime(capex.capex_date) <= pd.Timestamp(opening_date)], opening_date)
    assets = C.OPENING_CASH + sim.opening["ar"] + sim.opening["inventory"] + fa_net
    liabilities = sim.opening["ap"] + opening_loan
    t["opening_balances"] = pd.DataFrame([
        {"item": "cash", "as_of_date": opening_date, "amount": C.OPENING_CASH, "note": "Bank and cash balance (assumption)"},
        {"item": "accounts_receivable", "as_of_date": opening_date, "amount": sim.opening["ar"], "note": "Business-customer invoices outstanding (see opening_receivables)"},
        {"item": "inventory", "as_of_date": opening_date, "amount": sim.opening["inventory"], "note": "Sum of 'opening' stock movements at initial unit cost"},
        {"item": "fixed_assets_net", "as_of_date": opening_date, "amount": fa_net, "note": "Pre-period capex less straight-line depreciation"},
        {"item": "accounts_payable", "as_of_date": opening_date, "amount": sim.opening["ap"], "note": "Opening supplier invoices (is_opening = 1)"},
        {"item": "loan_principal", "as_of_date": opening_date, "amount": round(opening_loan, 2), "note": "Outstanding principal of loans started before the period"},
        {"item": "owner_equity", "as_of_date": opening_date, "amount": round(assets - liabilities, 2), "note": "Balancing figure: assets minus liabilities"},
    ])
    capex_in_period = capex[pd.to_datetime(capex.capex_date) >= pd.Timestamp(cfg.start_date)]
    t["cash_ledger"] = _cash_ledger(t, capex_in_period, t["loans"], pd.Timestamp(cfg.start_date))
    planned = [p for p in C.PLANNED_CASH_ITEMS if p["planned_date"] > cfg.end_date]
    t["planned_cash_items"] = pd.DataFrame(planned, columns=["planned_item_id", "planned_date", "category", "description", "amount", "activity"])
    t["stores"] = pd.DataFrame([{k: s[k] for k in ["store_id", "store_name", "city", "opened_date", "size_sqft"]} for s in C.STORES])
    t["suppliers"] = pd.DataFrame(C.SUPPLIERS)
    t["products"] = pd.DataFrame([{k: p[k] for k in ["product_id", "sku", "product_name", "category", "supplier_id", "unit_price",
                                                       "initial_unit_cost", "case_pack", "max_units_per_order"]} for p in C.PRODUCTS])
    t["meta"] = pd.DataFrame([
        ("simulated", "true"), ("seed", str(cfg.seed)), ("generator_version", GENERATOR_VERSION),
        ("start_date", cfg.start_date.isoformat()), ("end_date", cfg.end_date.isoformat()), ("months", str(cfg.months)),
        ("base_currency", "INR"), ("amounts_exclude_tax", "true"),
        ("description", "Synthetic two-store retailer generated by RetailIQ. Not real business data."),
    ], columns=["key", "value"])

    # dates -> ISO text, deterministic ordering
    from ..db.schema import DATE_COLUMNS
    for name, df in t.items():
        for col in DATE_COLUMNS.get(name, []):
            if col in df.columns:
                df[col] = df[col].map(_iso)
    return t
