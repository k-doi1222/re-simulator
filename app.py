"""アプリの入口。認証してから、ページ遷移をStreamlitに任せる。"""
import streamlit as st

from auth import require_password

st.set_page_config(page_title="不動産投資シミュレーション",
                   page_icon="🏢", layout="wide")

require_password()

pg = st.navigation({
    "物件": [
        st.Page("pages/list.py", title="物件一覧", icon=":material/list:", default=True),
        st.Page("pages/detail.py", title="物件詳細", icon=":material/apartment:"),
        st.Page("pages/new.py", title="物件を登録", icon=":material/add:"),
    ],
    "取引先": [
        st.Page("pages/banks.py", title="銀行", icon=":material/account_balance:"),
        st.Page("pages/brokers.py", title="仲介業者", icon=":material/handshake:"),
    ],
    "振り返り": [
        st.Page("pages/analytics.py", title="分析", icon=":material/insights:"),
    ],
})
pg.run()
