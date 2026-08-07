"""アプリの入口。認証してから、ページ遷移をStreamlitに任せる。"""
import streamlit as st

from auth import require_password

st.set_page_config(page_title="不動産投資シミュレーション",
                   page_icon="🏢", layout="wide")

require_password()

list_page = st.Page("pages/list.py", title="物件一覧", icon="📋", default=True)
detail_page = st.Page("pages/detail.py", title="物件詳細", icon="🏢")

pg = st.navigation([list_page, detail_page])
pg.run()
