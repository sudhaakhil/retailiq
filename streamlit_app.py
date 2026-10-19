"""RetailIQ - entry point.  Run with:  streamlit run streamlit_app.py

All data shown is SIMULATED. Pages live in app_pages/; shared logic lives in the retailiq package.
"""
import streamlit as st

st.set_page_config(page_title="RetailIQ", page_icon=":material/storefront:", layout="wide", initial_sidebar_state="expanded")

from retailiq.ui import state  # noqa: E402  (imported after set_page_config on purpose)

pages = {
    "Overview": [
        st.Page("app_pages/overview.py", title="Executive overview", icon=":material/dashboard:", default=True),
        st.Page("app_pages/action_center.py", title="Action center", icon=":material/notifications_active:"),
    ],
    "Performance": [
        st.Page("app_pages/sales.py", title="Sales", icon=":material/point_of_sale:"),
        st.Page("app_pages/finance.py", title="Finance & profitability", icon=":material/account_balance:"),
        st.Page("app_pages/working_capital.py", title="Working capital", icon=":material/water_drop:"),
    ],
    "Operations": [
        st.Page("app_pages/inventory.py", title="Inventory & procurement", icon=":material/inventory_2:"),
        st.Page("app_pages/cash_funding.py", title="Cash forecast & funding", icon=":material/payments:"),
    ],
    "Growth": [
        st.Page("app_pages/marketing.py", title="Marketing", icon=":material/campaign:"),
        st.Page("app_pages/customers.py", title="Customers", icon=":material/groups:"),
        st.Page("app_pages/reviews.py", title="Customer reviews", icon=":material/reviews:"),
    ],
    "Planning": [
        st.Page("app_pages/forecasting.py", title="Sales forecasting", icon=":material/trending_up:"),
        st.Page("app_pages/integrated_scenario.py", title="Connected scenario", icon=":material/account_tree:"),
    ],
    "Reference": [
        st.Page("app_pages/data_definitions.py", title="Data, definitions & checks", icon=":material/menu_book:"),
    ],
}

page = st.navigation(pages, position="sidebar", expanded=True)
state.render_sidebar()
st.title(page.title, icon=page.icon)
st.caption(":material/science: **Simulated data** - every number in this app comes from a reproducible synthetic dataset, not a real business.")
page.run()
