"""Database schema (SQLAlchemy Core).

Every table documents its GRAIN (what one row represents) and its keys.
Dates are stored as ISO text ``YYYY-MM-DD`` so the SQLite file is easy to
inspect with any tool. Money is stored in the base currency (INR).
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

DateCol = lambda name, **kw: Column(name, String(10), **kw)  # noqa: E731  ISO date text

# ---------------------------------------------------------------- reference data
meta = Table(
    "meta",
    metadata,
    Column("key", String(50), primary_key=True),
    Column("value", Text, nullable=False),
    info={"grain": "One row per dataset property (seed, version, period, simulated flag)."},
)

stores = Table(
    "stores",
    metadata,
    Column("store_id", String(10), primary_key=True),
    Column("store_name", String(80), nullable=False),
    Column("city", String(50), nullable=False),
    DateCol("opened_date", nullable=False),
    Column("size_sqft", Integer, nullable=False),
    info={"grain": "One row per store."},
)

suppliers = Table(
    "suppliers",
    metadata,
    Column("supplier_id", String(10), primary_key=True),
    Column("supplier_name", String(80), nullable=False),
    Column("lead_time_days", Integer, nullable=False),
    Column("on_time_rate", Float, nullable=False),
    Column("payment_terms_days", Integer, nullable=False),
    info={"grain": "One row per supplier. lead_time_days is the quoted (nominal) lead time."},
)

products = Table(
    "products",
    metadata,
    Column("product_id", String(10), primary_key=True),
    Column("sku", String(20), nullable=False, unique=True),
    Column("product_name", String(80), nullable=False),
    Column("category", String(40), nullable=False),
    Column("supplier_id", String(10), ForeignKey("suppliers.supplier_id"), nullable=False),
    Column("unit_price", Float, nullable=False),
    Column("initial_unit_cost", Float, nullable=False),
    Column("case_pack", Integer, nullable=False),
    Column("max_units_per_order", Integer, nullable=False),
    info={"grain": "One row per product (SKU). unit_price is the list price before discounts."},
)

campaigns = Table(
    "campaigns",
    metadata,
    Column("campaign_id", String(10), primary_key=True),
    Column("campaign_name", String(80), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("objective", String(20), nullable=False),
    DateCol("start_date", nullable=False),
    DateCol("end_date", nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=True),
    Column("target_category", String(40), nullable=True),
    Column("discount_pct", Float, nullable=False),
    Column("planned_budget", Float, nullable=False),
    Column("status", String(12), nullable=False),
    info={"grain": "One row per campaign. store_id/target_category NULL means all stores/categories."},
)

customers = Table(
    "customers",
    metadata,
    Column("customer_id", String(10), primary_key=True),
    Column("customer_name", String(80), nullable=False),
    Column("customer_type", String(10), nullable=False),  # retail | business
    Column("home_store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    DateCol("registration_date", nullable=False),
    Column("acquisition_channel", String(30), nullable=False),
    Column("acquisition_campaign_id", String(10), ForeignKey("campaigns.campaign_id"), nullable=True),
    Column("credit_terms_days", Integer, nullable=False),
    info={"grain": "One row per registered (loyalty or business) customer."},
)

# ---------------------------------------------------------------- sales
sales_transactions = Table(
    "sales_transactions",
    metadata,
    Column("transaction_id", String(12), primary_key=True),
    DateCol("transaction_date", nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    Column("customer_id", String(10), ForeignKey("customers.customer_id"), nullable=True),
    Column("channel", String(12), nullable=False),  # in_store | online
    Column("payment_method", String(10), nullable=False),  # cash | upi | card | credit
    Column("campaign_id", String(10), ForeignKey("campaigns.campaign_id"), nullable=True),
    Column("gross_amount", Float, nullable=False),
    Column("discount_amount", Float, nullable=False),
    Column("net_amount", Float, nullable=False),
    DateCol("due_date", nullable=False),
    info={
        "grain": "One row per sales transaction (basket). Amounts exclude GST. "
        "customer_id NULL = anonymous walk-in. campaign_id = single attributed campaign (or NULL)."
    },
)
Index("ix_txn_date", sales_transactions.c.transaction_date)

sales_lines = Table(
    "sales_lines",
    metadata,
    Column("line_id", String(14), primary_key=True),
    Column("transaction_id", String(12), ForeignKey("sales_transactions.transaction_id"), nullable=False),
    Column("product_id", String(10), ForeignKey("products.product_id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Float, nullable=False),
    Column("discount_amount", Float, nullable=False),
    Column("net_amount", Float, nullable=False),
    Column("unit_cost", Float, nullable=False),
    Column("cogs_amount", Float, nullable=False),
    info={"grain": "One row per product within a transaction. unit_cost = weighted-average cost at sale time."},
)
Index("ix_lines_txn", sales_lines.c.transaction_id)

returns = Table(
    "returns",
    metadata,
    Column("return_id", String(12), primary_key=True),
    Column("line_id", String(14), ForeignKey("sales_lines.line_id"), nullable=False, unique=True),
    Column("transaction_id", String(12), ForeignKey("sales_transactions.transaction_id"), nullable=False),
    DateCol("return_date", nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    Column("product_id", String(10), ForeignKey("products.product_id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("refund_amount", Float, nullable=False),
    Column("cogs_reversal", Float, nullable=False),
    Column("reason", String(30), nullable=False),
    info={"grain": "One row per returned sales line (full-line returns). Refund paid on return_date."},
)

customer_receipts = Table(
    "customer_receipts",
    metadata,
    Column("receipt_id", String(12), primary_key=True),
    DateCol("receipt_date", nullable=False),
    Column("transaction_id", String(12), ForeignKey("sales_transactions.transaction_id"), nullable=True),
    Column("opening_receivable_id", String(10), ForeignKey("opening_receivables.opening_receivable_id"), nullable=True),
    Column("customer_id", String(10), ForeignKey("customers.customer_id"), nullable=True),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    Column("payment_method", String(10), nullable=False),
    Column("amount", Float, nullable=False),
    info={"grain": "One row per cash collection (settlement) of a transaction or opening receivable."},
)

opening_receivables = Table(
    "opening_receivables",
    metadata,
    Column("opening_receivable_id", String(10), primary_key=True),
    Column("customer_id", String(10), ForeignKey("customers.customer_id"), nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    DateCol("invoice_date", nullable=False),
    DateCol("due_date", nullable=False),
    Column("amount", Float, nullable=False),
    info={"grain": "One row per business-customer invoice outstanding at the opening date."},
)

# ---------------------------------------------------------------- procurement & inventory
purchase_orders = Table(
    "purchase_orders",
    metadata,
    Column("po_id", String(12), primary_key=True),
    Column("supplier_id", String(10), ForeignKey("suppliers.supplier_id"), nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    DateCol("order_date", nullable=False),
    DateCol("expected_date", nullable=False),
    DateCol("received_date", nullable=True),
    Column("status", String(10), nullable=False),  # received | open
    Column("total_cost", Float, nullable=False),
    info={"grain": "One row per purchase order to one supplier for one store."},
)

purchase_order_lines = Table(
    "purchase_order_lines",
    metadata,
    Column("po_line_id", String(14), primary_key=True),
    Column("po_id", String(12), ForeignKey("purchase_orders.po_id"), nullable=False),
    Column("product_id", String(10), ForeignKey("products.product_id"), nullable=False),
    Column("quantity_ordered", Integer, nullable=False),
    Column("quantity_received", Integer, nullable=False),
    Column("unit_cost", Float, nullable=False),
    Column("line_cost", Float, nullable=False),
    info={"grain": "One row per product on a purchase order."},
)

supplier_invoices = Table(
    "supplier_invoices",
    metadata,
    Column("invoice_id", String(12), primary_key=True),
    Column("supplier_id", String(10), ForeignKey("suppliers.supplier_id"), nullable=False),
    Column("po_id", String(12), ForeignKey("purchase_orders.po_id"), nullable=True),
    DateCol("invoice_date", nullable=False),
    DateCol("due_date", nullable=False),
    Column("amount", Float, nullable=False),
    Column("is_opening", Integer, nullable=False),
    info={"grain": "One row per supplier invoice (raised when goods are received; opening invoices flagged)."},
)

supplier_payments = Table(
    "supplier_payments",
    metadata,
    Column("payment_id", String(12), primary_key=True),
    Column("invoice_id", String(12), ForeignKey("supplier_invoices.invoice_id"), nullable=False),
    Column("supplier_id", String(10), ForeignKey("suppliers.supplier_id"), nullable=False),
    DateCol("payment_date", nullable=False),
    Column("amount", Float, nullable=False),
    info={"grain": "One row per payment to a supplier against one invoice."},
)

stock_movements = Table(
    "stock_movements",
    metadata,
    Column("movement_id", String(14), primary_key=True),
    DateCol("movement_date", nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    Column("product_id", String(10), ForeignKey("products.product_id"), nullable=False),
    Column("movement_type", String(10), nullable=False),  # opening | receipt | sale | return
    Column("quantity", Integer, nullable=False),  # signed: + into stock, - out of stock
    Column("unit_cost", Float, nullable=False),
    Column("value", Float, nullable=False),  # signed, at cost
    Column("reference_id", String(14), nullable=True),
    info={"grain": "One row per stock movement of one product at one store."},
)
Index("ix_mov_date", stock_movements.c.movement_date)

inventory_daily = Table(
    "inventory_daily",
    metadata,
    DateCol("balance_date", primary_key=True),
    Column("store_id", String(10), ForeignKey("stores.store_id"), primary_key=True),
    Column("product_id", String(10), ForeignKey("products.product_id"), primary_key=True),
    Column("quantity_on_hand", Integer, nullable=False),
    Column("inventory_value", Float, nullable=False),
    Column("avg_unit_cost", Float, nullable=False),
    Column("quantity_on_order", Integer, nullable=False),
    info={"grain": "One row per store x product x day (end-of-day balance at weighted-average cost)."},
)

# ---------------------------------------------------------------- costs, assets, financing
operating_expenses = Table(
    "operating_expenses",
    metadata,
    Column("expense_id", String(12), primary_key=True),
    DateCol("expense_date", nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=True),
    Column("category", String(40), nullable=False),
    Column("cost_behaviour", String(10), nullable=False),  # fixed | variable
    Column("campaign_id", String(10), ForeignKey("campaigns.campaign_id"), nullable=True),
    Column("description", String(120), nullable=False),
    Column("amount", Float, nullable=False),
    info={
        "grain": "One row per expense. Recognised and paid on expense_date (no accruals). "
        "store_id NULL = shared head-office cost. Excludes depreciation (derived from capex)."
    },
)

capital_expenditures = Table(
    "capital_expenditures",
    metadata,
    Column("capex_id", String(10), primary_key=True),
    DateCol("capex_date", nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=True),
    Column("description", String(120), nullable=False),
    Column("amount", Float, nullable=False),
    Column("useful_life_months", Integer, nullable=False),
    info={"grain": "One row per fixed-asset purchase (incl. assets bought before the opening date)."},
)

loans = Table(
    "loans",
    metadata,
    Column("loan_id", String(10), primary_key=True),
    Column("lender", String(80), nullable=False),
    DateCol("start_date", nullable=False),
    Column("principal", Float, nullable=False),
    Column("annual_rate", Float, nullable=False),
    Column("term_months", Integer, nullable=False),
    Column("payment_day", Integer, nullable=False),
    info={"grain": "One row per term loan (equal monthly instalments)."},
)

loan_payments = Table(
    "loan_payments",
    metadata,
    Column("loan_payment_id", String(12), primary_key=True),
    Column("loan_id", String(10), ForeignKey("loans.loan_id"), nullable=False),
    DateCol("payment_date", nullable=False),
    Column("instalment_no", Integer, nullable=False),
    Column("interest_amount", Float, nullable=False),
    Column("principal_amount", Float, nullable=False),
    Column("balance_after", Float, nullable=False),
    info={"grain": "One row per loan instalment paid within the simulated period."},
)

equity_transactions = Table(
    "equity_transactions",
    metadata,
    Column("equity_txn_id", String(10), primary_key=True),
    DateCol("txn_date", nullable=False),
    Column("txn_type", String(20), nullable=False),  # owner_investment | owner_drawing
    Column("amount", Float, nullable=False),  # always positive; direction given by txn_type
    Column("description", String(120), nullable=False),
    info={"grain": "One row per owner investment or owner drawing."},
)

opening_balances = Table(
    "opening_balances",
    metadata,
    Column("item", String(40), primary_key=True),
    DateCol("as_of_date", nullable=False),
    Column("amount", Float, nullable=False),
    Column("note", String(200), nullable=False),
    info={"grain": "One row per balance-sheet item at the close of the day before the first simulated day."},
)

budgets = Table(
    "budgets",
    metadata,
    DateCol("month", primary_key=True),
    Column("store_id", String(10), primary_key=True),  # 'HQ' for shared costs
    Column("line_item", String(30), primary_key=True),  # net_sales | cogs | operating_expenses
    Column("amount", Float, nullable=False),
    info={"grain": "One row per month x store (or HQ) x budget line."},
)

cash_ledger = Table(
    "cash_ledger",
    metadata,
    Column("cash_txn_id", String(14), primary_key=True),
    DateCol("txn_date", nullable=False),
    Column("activity", String(10), nullable=False),  # operating | investing | financing
    Column("category", String(40), nullable=False),
    Column("amount", Float, nullable=False),  # signed: + inflow, - outflow
    Column("source_table", String(30), nullable=False),
    Column("source_id", String(14), nullable=False),
    Column("store_id", String(10), nullable=True),
    info={"grain": "One row per cash movement, derived from the source tables listed in source_table."},
)
Index("ix_cash_date", cash_ledger.c.txn_date)

planned_cash_items = Table(
    "planned_cash_items",
    metadata,
    Column("planned_item_id", String(10), primary_key=True),
    DateCol("planned_date", nullable=False),
    Column("category", String(40), nullable=False),
    Column("description", String(120), nullable=False),
    Column("amount", Float, nullable=False),  # signed: + inflow, - outflow
    Column("activity", String(10), nullable=False),
    info={"grain": "One row per known future cash commitment (planning assumption, editable in the UI)."},
)

# ---------------------------------------------------------------- marketing & reviews
campaign_daily = Table(
    "campaign_daily",
    metadata,
    Column("campaign_id", String(10), ForeignKey("campaigns.campaign_id"), primary_key=True),
    DateCol("activity_date", primary_key=True),
    Column("spend", Float, nullable=False),
    Column("impressions", Integer, nullable=False),
    Column("clicks", Integer, nullable=False),
    info={"grain": "One row per campaign x day of delivered media."},
)

reviews = Table(
    "reviews",
    metadata,
    Column("review_id", String(10), primary_key=True),
    DateCol("review_date", nullable=False),
    Column("customer_id", String(10), ForeignKey("customers.customer_id"), nullable=False),
    Column("product_id", String(10), ForeignKey("products.product_id"), nullable=False),
    Column("store_id", String(10), ForeignKey("stores.store_id"), nullable=False),
    Column("transaction_id", String(12), ForeignKey("sales_transactions.transaction_id"), nullable=False),
    Column("rating", Integer, nullable=False),
    Column("review_text", Text, nullable=False),
    info={"grain": "One row per customer review of one purchased product."},
)

# Insert order respects foreign keys.
TABLE_ORDER = [
    "meta",
    "stores",
    "suppliers",
    "products",
    "campaigns",
    "customers",
    "sales_transactions",
    "sales_lines",
    "returns",
    "opening_receivables",
    "customer_receipts",
    "purchase_orders",
    "purchase_order_lines",
    "supplier_invoices",
    "supplier_payments",
    "stock_movements",
    "inventory_daily",
    "operating_expenses",
    "capital_expenditures",
    "loans",
    "loan_payments",
    "equity_transactions",
    "opening_balances",
    "budgets",
    "cash_ledger",
    "planned_cash_items",
    "campaign_daily",
    "reviews",
]

DATE_COLUMNS = {
    t.name: [c.name for c in t.columns if isinstance(c.type, String) and c.type.length == 10 and (
        c.name.endswith("_date") or c.name in {"month"}
    )]
    for t in metadata.sorted_tables
}
