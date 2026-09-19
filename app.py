"""アプリの入口。認証してから、ページ遷移をStreamlitに任せる。"""
import streamlit as st

from auth import require_password
from db import query

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
        st.Page("pages/sales_brokers.py", title="売買仲介", icon=":material/handshake:"),
        st.Page("pages/rental_agencies.py", title="賃貸仲介", icon=":material/key:"),
    ],
    "振り返り": [
        st.Page("pages/analytics.py", title="分析", icon=":material/insights:"),
    ],
})
# DBの読み込みは5分間キャッシュしている。アプリの外（SQLなど）でDBを直したときに、
# ブラウザを読み込み直さず（ログインや選択中の画面を保ったまま）最新にするためのボタン。
with st.sidebar:
    if st.button("最新の情報に更新", icon=":material/refresh:", width="stretch",
                 help="DBの内容を読み直します。今の画面と選択はそのまま残ります"):
        query.clear()
        st.rerun()

pg.run()
