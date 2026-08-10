"""賃貸仲介 — UC-5 賃貸ヒアリング

現地の賃貸仲介店で聞いた声。物件が変わってもエリアの知見は効くので、
内容やエリアの言葉でも検索できるようにしてある。
"""
import streamlit as st

from auth import require_password
from partners import (render_add_interaction, render_history,
                      render_office_jump_panel, render_offices, render_persons)
from theme import compact_css

require_password()
compact_css()

st.markdown("### 賃貸仲介")

# 物件詳細から「この相手先を直す」で来たときは、タブの外に編集欄を出す
# （Streamlit はタブを自動で開けないため）
render_office_jump_panel("rental_agency")

tab_co, tab_hist, tab_person, tab_add = st.tabs(
    ["会社・拠点", "聞いた話", "担当者", "記録する"])

with tab_co:
    render_offices("rental_agency", show_referrals=False)

with tab_hist:
    render_history(["rental_hearing"], "rental_agency",
                   hint="例：美濃加茂 / 外国人 / 空室")

with tab_person:
    render_persons("rental_agency")

with tab_add:
    render_add_interaction("rental_agency", {"賃貸ヒアリング": "rental_hearing"})
