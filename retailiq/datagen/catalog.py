"""Static reference data and behavioural assumptions for the simulation.

Every name here is fictional and every number is an assumption chosen to make
the simulated business behave plausibly. Nothing describes a real company.
"""
from __future__ import annotations

import datetime as dt

D = dt.date

STORES = [
    {"store_id": "S01", "store_name": "RetailIQ Central (simulated)", "city": "Bengaluru",
     "opened_date": "2021-06-01", "size_sqft": 2400, "base_txn": 30.0, "growth": 0.10, "planned_growth": 0.08,
     "online_share": 0.12},
    {"store_id": "S02", "store_name": "RetailIQ Lakeside (simulated)", "city": "Pune",
     "opened_date": "2023-04-01", "size_sqft": 1800, "base_txn": 21.0, "growth": 0.20, "planned_growth": 0.18,
     "online_share": 0.18},
]

# supplier_id, name, nominal lead time (days), on-time probability, payment terms (days)
SUPPLIERS = [
    {"supplier_id": "SUP01", "supplier_name": "Volt Accessories Distribution (sim)", "lead_time_days": 10, "on_time_rate": 0.78, "payment_terms_days": 30},
    {"supplier_id": "SUP02", "supplier_name": "HomeNest Wholesale (sim)", "lead_time_days": 7, "on_time_rate": 0.90, "payment_terms_days": 30},
    {"supplier_id": "SUP03", "supplier_name": "Purebloom Care Products (sim)", "lead_time_days": 5, "on_time_rate": 0.95, "payment_terms_days": 21},
    {"supplier_id": "SUP04", "supplier_name": "Loomline Apparel (sim)", "lead_time_days": 14, "on_time_rate": 0.72, "payment_terms_days": 45},
    {"supplier_id": "SUP05", "supplier_name": "Inkleaf Stationery (sim)", "lead_time_days": 4, "on_time_rate": 0.97, "payment_terms_days": 15},
    {"supplier_id": "SUP06", "supplier_name": "Stride Fitness Supply (sim)", "lead_time_days": 12, "on_time_rate": 0.85, "payment_terms_days": 30},
]

# Supplier price increases (effective date, % increase on purchase cost).
SUPPLIER_PRICE_CHANGES = {
    "SUP01": [(D(2025, 10, 1), 0.06)],
    "SUP04": [(D(2026, 4, 1), 0.05)],
}

# demand = relative popularity (approx. units/day at a base store), qty_extra = mean extra units per line
PRODUCTS = [
    # Electronics Accessories
    {"product_id": "P01", "sku": "EA-CHG-30W", "product_name": "USB-C Fast Charger 30W", "category": "Electronics Accessories", "supplier_id": "SUP01", "unit_price": 999, "initial_unit_cost": 520, "demand": 1.4, "qty_extra": 0.1, "case_pack": 10, "max_units_per_order": 300, "quality": 4.2, "return_rate": 0.035},
    {"product_id": "P02", "sku": "EA-EBD-TWS", "product_name": "Wireless Earbuds", "category": "Electronics Accessories", "supplier_id": "SUP01", "unit_price": 1999, "initial_unit_cost": 1150, "demand": 0.9, "qty_extra": 0.05, "case_pack": 6, "max_units_per_order": 150, "quality": 3.9, "return_rate": 0.05},
    {"product_id": "P03", "sku": "EA-PWB-10K", "product_name": "Power Bank 10000mAh", "category": "Electronics Accessories", "supplier_id": "SUP01", "unit_price": 1399, "initial_unit_cost": 780, "demand": 0.8, "qty_extra": 0.05, "case_pack": 6, "max_units_per_order": 150, "quality": 4.1, "return_rate": 0.04},
    {"product_id": "P04", "sku": "EA-SPK-MINI", "product_name": "Bluetooth Speaker Mini", "category": "Electronics Accessories", "supplier_id": "SUP01", "unit_price": 2299, "initial_unit_cost": 1400, "demand": 0.35, "qty_extra": 0.0, "case_pack": 4, "max_units_per_order": 60, "quality": 4.0, "return_rate": 0.04},
    # Home & Kitchen
    {"product_id": "P05", "sku": "HK-BTL-1L", "product_name": "Steel Water Bottle 1L", "category": "Home & Kitchen", "supplier_id": "SUP02", "unit_price": 449, "initial_unit_cost": 210, "demand": 2.0, "qty_extra": 0.3, "case_pack": 12, "max_units_per_order": 400, "quality": 4.4, "return_rate": 0.015},
    {"product_id": "P06", "sku": "HK-PAN-24", "product_name": "Non-stick Frying Pan 24cm", "category": "Home & Kitchen", "supplier_id": "SUP02", "unit_price": 1199, "initial_unit_cost": 640, "demand": 0.7, "qty_extra": 0.0, "case_pack": 6, "max_units_per_order": 120, "quality": 4.1, "return_rate": 0.02},
    {"product_id": "P07", "sku": "HK-JAR-SET", "product_name": "Glass Storage Jar Set", "category": "Home & Kitchen", "supplier_id": "SUP02", "unit_price": 749, "initial_unit_cost": 380, "demand": 0.9, "qty_extra": 0.1, "case_pack": 6, "max_units_per_order": 150, "quality": 4.3, "return_rate": 0.02},
    {"product_id": "P08", "sku": "HK-MUG-2", "product_name": "Ceramic Coffee Mug (Set of 2)", "category": "Home & Kitchen", "supplier_id": "SUP02", "unit_price": 399, "initial_unit_cost": 190, "demand": 1.5, "qty_extra": 0.2, "case_pack": 12, "max_units_per_order": 300, "quality": 4.3, "return_rate": 0.015},
    # Personal Care
    {"product_id": "P09", "sku": "PC-FWH-150", "product_name": "Herbal Face Wash 150ml", "category": "Personal Care", "supplier_id": "SUP03", "unit_price": 199, "initial_unit_cost": 95, "demand": 3.2, "qty_extra": 0.4, "case_pack": 24, "max_units_per_order": 600, "quality": 4.2, "return_rate": 0.008},
    {"product_id": "P10", "sku": "PC-LOT-400", "product_name": "Aloe Vera Body Lotion 400ml", "category": "Personal Care", "supplier_id": "SUP03", "unit_price": 329, "initial_unit_cost": 160, "demand": 2.2, "qty_extra": 0.3, "case_pack": 24, "max_units_per_order": 500, "quality": 4.2, "return_rate": 0.008},
    {"product_id": "P11", "sku": "PC-TRM-ELC", "product_name": "Electric Trimmer", "category": "Personal Care", "supplier_id": "SUP03", "unit_price": 1599, "initial_unit_cost": 900, "demand": 0.5, "qty_extra": 0.0, "case_pack": 4, "max_units_per_order": 80, "quality": 3.3, "return_rate": 0.05},
    {"product_id": "P12", "sku": "PC-TBR-B4", "product_name": "Bamboo Toothbrush (4 pack)", "category": "Personal Care", "supplier_id": "SUP03", "unit_price": 179, "initial_unit_cost": 70, "demand": 2.6, "qty_extra": 0.3, "case_pack": 24, "max_units_per_order": 600, "quality": 4.4, "return_rate": 0.005},
    # Apparel
    {"product_id": "P13", "sku": "AP-TSH-CRW", "product_name": "Cotton Crew T-Shirt", "category": "Apparel", "supplier_id": "SUP04", "unit_price": 599, "initial_unit_cost": 260, "demand": 2.4, "qty_extra": 0.4, "case_pack": 20, "max_units_per_order": 500, "quality": 4.1, "return_rate": 0.06},
    {"product_id": "P14", "sku": "AP-JNS-SLM", "product_name": "Denim Jeans Slim Fit", "category": "Apparel", "supplier_id": "SUP04", "unit_price": 1799, "initial_unit_cost": 820, "demand": 0.9, "qty_extra": 0.05, "case_pack": 10, "max_units_per_order": 200, "quality": 4.0, "return_rate": 0.08},
    {"product_id": "P15", "sku": "AP-HOD-WNT", "product_name": "Winter Hoodie", "category": "Apparel", "supplier_id": "SUP04", "unit_price": 1499, "initial_unit_cost": 690, "demand": 0.6, "qty_extra": 0.05, "case_pack": 10, "max_units_per_order": 200, "quality": 4.2, "return_rate": 0.06},
    {"product_id": "P16", "sku": "AP-SCK-3P", "product_name": "Cotton Socks (3 pairs)", "category": "Apparel", "supplier_id": "SUP04", "unit_price": 249, "initial_unit_cost": 90, "demand": 2.0, "qty_extra": 0.4, "case_pack": 30, "max_units_per_order": 600, "quality": 4.3, "return_rate": 0.01},
    # Stationery & Office
    {"product_id": "P17", "sku": "ST-NBK-A5", "product_name": "A5 Ruled Notebook (Pack of 3)", "category": "Stationery & Office", "supplier_id": "SUP05", "unit_price": 199, "initial_unit_cost": 85, "demand": 3.0, "qty_extra": 0.5, "case_pack": 30, "max_units_per_order": 800, "quality": 4.4, "return_rate": 0.005},
    {"product_id": "P18", "sku": "ST-PEN-10", "product_name": "Gel Pen Set (10)", "category": "Stationery & Office", "supplier_id": "SUP05", "unit_price": 149, "initial_unit_cost": 60, "demand": 2.6, "qty_extra": 0.5, "case_pack": 40, "max_units_per_order": 800, "quality": 4.3, "return_rate": 0.005},
    {"product_id": "P19", "sku": "ST-ORG-DSK", "product_name": "Desk Organizer", "category": "Stationery & Office", "supplier_id": "SUP05", "unit_price": 599, "initial_unit_cost": 280, "demand": 0.5, "qty_extra": 0.0, "case_pack": 6, "max_units_per_order": 120, "quality": 4.2, "return_rate": 0.015},
    # Sports & Fitness
    {"product_id": "P20", "sku": "SF-YGM-6MM", "product_name": "Yoga Mat 6mm", "category": "Sports & Fitness", "supplier_id": "SUP06", "unit_price": 999, "initial_unit_cost": 450, "demand": 0.8, "qty_extra": 0.0, "case_pack": 8, "max_units_per_order": 160, "quality": 4.2, "return_rate": 0.02},
]

# Calibration assumption: the unit costs above were scaled up so the simulated business earns a
# gross margin near 40% and a thin operating margin, which keeps cash realistically tight.
COST_SCALE = 1.20
for _p in PRODUCTS:
    _p["initial_unit_cost"] = round(_p["initial_unit_cost"] * COST_SCALE)

# Products that business customers buy in bulk.
B2B_PRODUCTS = ["P05", "P07", "P08", "P09", "P10", "P12", "P17", "P18", "P19"]

MONTH_SEASON = {1: 0.92, 2: 0.86, 3: 0.95, 4: 0.98, 5: 1.04, 6: 0.97, 7: 0.92, 8: 0.98, 9: 1.0, 10: 1.06, 11: 1.10, 12: 1.14}
WEEKDAY_FACTOR = [0.88, 0.85, 0.90, 0.95, 1.05, 1.30, 1.25]  # Monday .. Sunday

CATEGORY_SEASON = {
    "Apparel": {10: 1.2, 11: 1.35, 12: 1.5, 1: 1.3, 4: 0.85, 5: 0.8, 6: 0.85},
    "Sports & Fitness": {1: 1.8, 2: 1.35, 3: 1.1, 7: 0.8},
    "Stationery & Office": {6: 1.9, 7: 1.35, 3: 1.15, 12: 0.85},
    "Personal Care": {4: 1.15, 5: 1.25, 6: 1.1},
    "Electronics Accessories": {10: 1.25, 11: 1.2},
    "Home & Kitchen": {10: 1.3, 11: 1.25},
}
# The hoodie is much more seasonal than the rest of apparel.
PRODUCT_SEASON = {"P15": {11: 2.2, 12: 3.0, 1: 2.6, 2: 1.2, 3: 0.4, 4: 0.15, 5: 0.1, 6: 0.15, 7: 0.25, 8: 0.35, 9: 0.6, 10: 1.0}}

DIWALI = {2023: D(2023, 11, 12), 2024: D(2024, 11, 1), 2025: D(2025, 10, 20), 2026: D(2026, 11, 8), 2027: D(2027, 10, 29)}

# Campaigns. traffic_uplift raises store footfall, category_uplift raises the chance that baskets
# contain the target category, redemption = share of eligible baskets that use the campaign code
# (that code is the attribution signal). Planned campaigns have no actuals yet.
CAMPAIGNS = [
    {"campaign_id": "C01", "campaign_name": "Diwali Festive Sale 2024", "channel": "Social Media", "objective": "sales", "start_date": D(2024, 10, 18), "end_date": D(2024, 11, 3), "store_id": None, "target_category": None, "discount_pct": 0.10, "planned_budget": 120000, "traffic_uplift": 0.12, "category_uplift": 0.0, "redemption": 0.22},
    {"campaign_id": "C02", "campaign_name": "Winter Apparel Push 2024", "channel": "Search Ads", "objective": "sales", "start_date": D(2024, 11, 25), "end_date": D(2024, 12, 22), "store_id": None, "target_category": "Apparel", "discount_pct": 0.15, "planned_budget": 60000, "traffic_uplift": 0.03, "category_uplift": 0.7, "redemption": 0.40},
    {"campaign_id": "C03", "campaign_name": "New Year Fitness 2025", "channel": "Social Media", "objective": "sales", "start_date": D(2025, 1, 1), "end_date": D(2025, 1, 20), "store_id": None, "target_category": "Sports & Fitness", "discount_pct": 0.20, "planned_budget": 40000, "traffic_uplift": 0.02, "category_uplift": 1.2, "redemption": 0.45},
    {"campaign_id": "C04", "campaign_name": "Loyalty SMS Reactivation 2025", "channel": "Email/SMS", "objective": "retention", "start_date": D(2025, 2, 10), "end_date": D(2025, 2, 28), "store_id": None, "target_category": None, "discount_pct": 0.05, "planned_budget": 15000, "traffic_uplift": 0.04, "category_uplift": 0.0, "redemption": 0.10},
    {"campaign_id": "C05", "campaign_name": "Summer Personal Care 2025", "channel": "Influencer", "objective": "sales", "start_date": D(2025, 4, 10), "end_date": D(2025, 5, 10), "store_id": None, "target_category": "Personal Care", "discount_pct": 0.10, "planned_budget": 70000, "traffic_uplift": 0.03, "category_uplift": 0.5, "redemption": 0.30},
    {"campaign_id": "C06", "campaign_name": "Back to School 2025", "channel": "Print/Local", "objective": "sales", "start_date": D(2025, 6, 1), "end_date": D(2025, 6, 25), "store_id": None, "target_category": "Stationery & Office", "discount_pct": 0.12, "planned_budget": 35000, "traffic_uplift": 0.04, "category_uplift": 0.8, "redemption": 0.35},
    {"campaign_id": "C07", "campaign_name": "Earbuds Mega Deal", "channel": "Search Ads", "objective": "sales", "start_date": D(2025, 7, 10), "end_date": D(2025, 7, 31), "store_id": None, "target_category": "Electronics Accessories", "discount_pct": 0.35, "planned_budget": 45000, "traffic_uplift": 0.06, "category_uplift": 2.8, "redemption": 0.75},
    {"campaign_id": "C08", "campaign_name": "Lakeside Store Awareness", "channel": "Social Media", "objective": "acquisition", "start_date": D(2025, 8, 1), "end_date": D(2025, 8, 31), "store_id": "S02", "target_category": None, "discount_pct": 0.05, "planned_budget": 50000, "traffic_uplift": 0.10, "category_uplift": 0.0, "redemption": 0.18},
    {"campaign_id": "C09", "campaign_name": "Diwali Festive Sale 2025", "channel": "Social Media", "objective": "sales", "start_date": D(2025, 10, 8), "end_date": D(2025, 10, 24), "store_id": None, "target_category": None, "discount_pct": 0.10, "planned_budget": 150000, "traffic_uplift": 0.14, "category_uplift": 0.0, "redemption": 0.24},
    {"campaign_id": "C10", "campaign_name": "Winter Apparel Push 2025", "channel": "Search Ads", "objective": "sales", "start_date": D(2025, 11, 20), "end_date": D(2025, 12, 20), "store_id": None, "target_category": "Apparel", "discount_pct": 0.15, "planned_budget": 70000, "traffic_uplift": 0.03, "category_uplift": 0.7, "redemption": 0.40},
    {"campaign_id": "C11", "campaign_name": "New Year Fitness 2026", "channel": "Social Media", "objective": "sales", "start_date": D(2026, 1, 1), "end_date": D(2026, 1, 20), "store_id": None, "target_category": "Sports & Fitness", "discount_pct": 0.20, "planned_budget": 45000, "traffic_uplift": 0.02, "category_uplift": 1.2, "redemption": 0.45},
    {"campaign_id": "C12", "campaign_name": "Home Makeover Month", "channel": "Influencer", "objective": "sales", "start_date": D(2026, 3, 1), "end_date": D(2026, 3, 31), "store_id": None, "target_category": "Home & Kitchen", "discount_pct": 0.12, "planned_budget": 90000, "traffic_uplift": 0.02, "category_uplift": 0.35, "redemption": 0.25},
    {"campaign_id": "C13", "campaign_name": "Back to School 2026", "channel": "Print/Local", "objective": "sales", "start_date": D(2026, 6, 1), "end_date": D(2026, 6, 25), "store_id": None, "target_category": "Stationery & Office", "discount_pct": 0.12, "planned_budget": 40000, "traffic_uplift": 0.04, "category_uplift": 0.8, "redemption": 0.35},
    {"campaign_id": "C14", "campaign_name": "Monsoon Electronics Flash Sale", "channel": "Search Ads", "objective": "sales", "start_date": D(2026, 7, 15), "end_date": D(2026, 8, 5), "store_id": None, "target_category": "Electronics Accessories", "discount_pct": 0.30, "planned_budget": 60000, "traffic_uplift": 0.05, "category_uplift": 2.2, "redemption": 0.70},
    {"campaign_id": "C15", "campaign_name": "Loyalty SMS Reactivation 2026", "channel": "Email/SMS", "objective": "retention", "start_date": D(2026, 8, 20), "end_date": D(2026, 9, 10), "store_id": None, "target_category": None, "discount_pct": 0.05, "planned_budget": 20000, "traffic_uplift": 0.04, "category_uplift": 0.0, "redemption": 0.10},
    {"campaign_id": "C16", "campaign_name": "Diwali Festive Sale 2026 (planned)", "channel": "Social Media", "objective": "sales", "start_date": D(2026, 10, 24), "end_date": D(2026, 11, 9), "store_id": None, "target_category": None, "discount_pct": 0.10, "planned_budget": 160000, "traffic_uplift": 0.14, "category_uplift": 0.0, "redemption": 0.24},
]

# Media cost assumptions: cost per 1,000 impressions (or per message/flyer) and click-through rate.
CHANNEL_MEDIA = {
    "Search Ads": {"cpm": 220.0, "ctr": 0.035},
    "Social Media": {"cpm": 110.0, "ctr": 0.012},
    "Influencer": {"cpm": 260.0, "ctr": 0.009},
    "Email/SMS": {"cpm": 250.0, "ctr": 0.025},   # ₹0.25 per message delivered
    "Print/Local": {"cpm": 1500.0, "ctr": 0.02},  # ₹1.50 per flyer; "clicks" = QR-code scans
}

# Fixed monthly cost plan: (category, store_id or None for head office, amount, day of month, from, to)
RENT = {"S01": [(D(2000, 1, 1), 85000), (D(2025, 10, 1), 89250)], "S02": [(D(2000, 1, 1), 65000), (D(2026, 4, 1), 68250)]}
SALARIES = {
    "S01": [(D(2000, 1, 1), 145000), (D(2026, 1, 1), 173000), (D(2026, 4, 1), 186840)],
    "S02": [(D(2000, 1, 1), 105000), (D(2026, 1, 1), 127000), (D(2026, 4, 1), 137160)],
    None: [(D(2000, 1, 1), 70000), (D(2026, 4, 1), 75600)],
}
UTILITIES_BASE = {"S01": 22000, "S02": 16000}
HQ_FIXED = [
    ("Software & accounting", 18000, 5),
    ("Insurance", 9000, 5),
    ("Office & administration", 8000, 15),
]
CARD_FEE_RATE = 0.018
DELIVERY_COST_PER_ORDER = 70.0
PACKAGING_RATE = 0.006

OPENING_CASH = 900000.0

CAPEX = [
    {"capex_id": "CX01", "capex_date": D(2021, 6, 1), "store_id": "S01", "description": "Central store fit-out (pre-period)", "amount": 1600000, "useful_life_months": 84},
    {"capex_id": "CX02", "capex_date": D(2023, 4, 1), "store_id": "S02", "description": "Lakeside store fit-out (pre-period)", "amount": 1400000, "useful_life_months": 84},
    {"capex_id": "CX03", "capex_date": D(2024, 4, 15), "store_id": None, "description": "POS and billing hardware (pre-period)", "amount": 240000, "useful_life_months": 36},
    {"capex_id": "CX04", "capex_date": D(2025, 1, 20), "store_id": "S01", "description": "Central store display shelving", "amount": 160000, "useful_life_months": 60},
    {"capex_id": "CX05", "capex_date": D(2025, 9, 20), "store_id": "S02", "description": "Lakeside store refurbishment", "amount": 650000, "useful_life_months": 60},
    {"capex_id": "CX06", "capex_date": D(2026, 2, 10), "store_id": None, "description": "Inventory software and barcode scanners", "amount": 220000, "useful_life_months": 36},
]

LOANS = [
    {"loan_id": "L01", "lender": "Term Loan A — existing (simulated lender)", "start_date": D(2023, 4, 5), "principal": 1800000, "annual_rate": 0.105, "term_months": 48, "payment_day": 5},
    {"loan_id": "L02", "lender": "Term Loan B — refurbishment (simulated lender)", "start_date": D(2025, 9, 12), "principal": 1500000, "annual_rate": 0.11, "term_months": 48, "payment_day": 12},
]

EQUITY = [
    {"equity_txn_id": "E01", "txn_date": D(2025, 3, 15), "txn_type": "owner_investment", "amount": 800000, "description": "Owner capital for second-store refurbishment"},
]
OWNER_DRAWING_QUARTERLY = 90000.0

PLANNED_CASH_ITEMS = [
    {"planned_item_id": "PL01", "planned_date": D(2026, 11, 16), "category": "Capital expenditure", "description": "Central store second-floor fit-out (planned)", "amount": -2800000, "activity": "investing"},
    {"planned_item_id": "PL02", "planned_date": D(2026, 12, 28), "category": "Owner drawings", "description": "Quarterly owner drawing (planned)", "amount": -90000, "activity": "financing"},
]

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
               "Ananya", "Diya", "Aadhya", "Saanvi", "Myra", "Kavya", "Isha", "Meera", "Priya", "Riya",
               "Rahul", "Neha", "Karan", "Pooja", "Siddharth", "Sneha", "Varun", "Divya", "Nikhil", "Lakshmi",
               "Farhan", "Zara", "Imran", "Ayesha", "Joseph", "Maria", "Harpreet", "Gurpreet", "Tenzin", "Anjali"]
LAST_NAMES = ["Sharma", "Iyer", "Reddy", "Patel", "Nair", "Rao", "Gupta", "Kulkarni", "Menon", "Das",
              "Singh", "Khan", "Joshi", "Pillai", "Banerjee", "Shetty", "Hegde", "Desai", "Fernandes", "Mehta"]
BUSINESS_PREFIX = ["Lotus", "Metro", "Greenleaf", "Sunbeam", "Bluebird", "Silverline", "Maple", "Orchid",
                   "Riverside", "Summit", "Harbour", "Cedar", "Pinnacle", "Brightway", "Northstar", "Saffron"]
BUSINESS_SUFFIX = ["Office Solutions", "Cafe", "Clinic", "Academy", "Co-working", "Guest House", "Traders", "Salon"]

RETURN_REASONS = {
    "Apparel": ["Size/fit", "Size/fit", "Changed mind", "Not as described"],
    "Electronics Accessories": ["Defective", "Changed mind", "Not as described"],
    "default": ["Changed mind", "Defective", "Not as described"],
}

# ---------------------------------------------------------------- review text templates
REVIEW_PHRASES = {
    ("quality", "pos"): ["Excellent quality, feels sturdy and well made.", "Works perfectly, very happy with the quality.",
                         "Good build quality and it has lasted well.", "Great product, exactly as described."],
    ("quality", "neg"): ["Poor quality, it stopped working within a week.", "The material feels cheap and flimsy.",
                         "Disappointed, it broke after a few uses.", "Not worth it, the quality is bad."],
    ("quality", "neu"): ["Quality is okay for daily use.", "Average quality, does the job.", "Decent product but nothing special."],
    ("defect", "neg"): ["The lid leaks badly and water spilled in my bag.", "Leaking from the cap after two days, poor quality.",
                        "Bottle started leaking, the seal seems defective."],
    ("price", "pos"): ["Great value for money.", "Good discount during the sale, worth it.", "Affordable and reasonably priced."],
    ("price", "neg"): ["Overpriced compared to other shops.", "Too expensive for what you get.", "Price is high, expected a better offer."],
    ("price", "neu"): ["Price is fair.", "Pricing is about average."],
    ("delivery", "pos"): ["Home delivery was quick and on time.", "Delivered the next day and well packed."],
    ("delivery", "neg"): ["Delivery was late by three days.", "Package arrived late and the box was damaged.",
                          "Delivery took much longer than promised."],
    ("delivery", "neu"): ["Delivery was fine."],
    ("availability", "pos"): ["Always in stock when I visit.", "Found everything I needed in stock."],
    ("availability", "neg"): ["It was out of stock twice before I could buy it.", "Had to come back again because it was unavailable.",
                              "Stock keeps running out, very frustrating."],
    ("availability", "neu"): ["Took a while to find in stock but got it eventually."],
    ("service", "pos"): ["Staff were helpful and friendly.", "Quick billing and polite staff.", "Excellent service at the store."],
    ("service", "neg"): ["Long queue at billing and only one counter open.", "Staff were rude and unhelpful.",
                         "Poor service, nobody was available to help."],
    ("service", "neu"): ["Service was okay."],
}
REVIEW_CLOSERS = {"pos": ["Would recommend.", "Will buy again.", ""], "neg": ["Will not buy again.", "Not happy.", ""], "neu": ["", ""]}

# Events that shape reviews.
DEFECT_PRODUCT = "P05"
DEFECT_START = D(2026, 3, 1)
SERVICE_ISSUE = {"store_id": "S02", "start": D(2026, 5, 1), "end": D(2026, 8, 31)}
