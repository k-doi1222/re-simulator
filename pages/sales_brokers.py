"""売買仲介 — UC-8 関係管理、UC-9 担当者の異動追随

物件を持ってきてくれる源泉なので、関係を切らさないことが大事。

画面は「1拠点のカルテ」を主役にしている。会社・担当者・やりとりは
3つでひとまとまりの情報なので、タブに分けると読みにくく、直すのにも辿り着けない。
横断で見たいもの（拠点一覧・やりとり一覧・担当者一覧）は下に畳んである。
"""
import streamlit as st

from auth import require_password
from nav import render_back_to_property
from office_card import render_office_card, render_office_picker
from partners import render_history, render_offices, render_persons
from theme import compact_css

require_password()
compact_css()

st.markdown("### 売買仲介")

# 物件詳細から飛んできたときだけ、戻るボタンを出す
render_back_to_property("sales_broker")

# ══ 拠点のカルテ（この画面の主役）══════════════════════════
office_id = render_office_picker("sales_broker")
if office_id:
    render_office_card("sales_broker", office_id)

st.divider()

# ══ 横断で見る ═════════════════════════════════════════════
with st.expander("会社・拠点の一覧（最終接触からの経過が長い順）"):
    render_offices("sales_broker", show_referrals=True)

with st.expander("やりとりを横断で見る"):
    render_history(["sales_contact"], "sales_broker",
                   hint="例：値下げ / 売り急ぎ / 買付")

with st.expander("担当者を横断で見る"):
    render_persons("sales_broker")
