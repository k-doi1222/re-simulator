"""売買仲介 — UC-8 関係管理、UC-9 担当者の異動追随

物件を持ってきてくれる源泉なので、関係を切らさないことが大事。
最終接触からの経過日数が長い先ほど上に出す。
"""
import streamlit as st

from auth import require_password
from partners import (render_add_interaction, render_history, render_offices,
                      render_persons)
from theme import compact_css

require_password()
compact_css()

st.markdown("### 売買仲介")

tab_co, tab_hist, tab_person, tab_add = st.tabs(
    ["会社・拠点", "やりとり", "担当者", "記録する"])

with tab_co:
    render_offices("sales_broker", show_referrals=True)

with tab_hist:
    render_history(["sales_contact"], "sales_broker",
                   hint="例：値下げ / 売り急ぎ / 買付")

with tab_person:
    render_persons("sales_broker")

with tab_add:
    render_add_interaction("sales_broker", {"売買仲介とのやりとり": "sales_contact"})
