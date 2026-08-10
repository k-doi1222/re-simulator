"""賃貸仲介 — UC-5 賃貸ヒアリング

現地の賃貸仲介店で聞いた声。物件が変わってもエリアの知見は効く。

画面は「1拠点のカルテ」を主役にしている。会社・担当者・聞いた話は
3つでひとまとまりの情報なので、タブに分けると読みにくく、直すのにも辿り着けない。
横断で見たいもの（拠点一覧・聞いた話の一覧・担当者一覧）は下に畳んである。
"""
import streamlit as st

from auth import require_password
from db import refresh_calc_cache
from nav import render_back_to_property
from office_card import render_office_card, render_office_picker
from partners import render_history, render_offices, render_persons
from theme import compact_css

require_password()
# 一覧に出す計算値は re_property_calc_cache から読む。
# 表示の前に、古くなったものだけ計算し直す（ふだんは0件で一瞬）。
refresh_calc_cache()
compact_css()

st.markdown("### 賃貸仲介")

# 物件詳細から飛んできたときだけ、戻るボタンを出す
render_back_to_property("rental_agency")

# ══ 拠点のカルテ（この画面の主役）══════════════════════════
office_id = render_office_picker("rental_agency")
if office_id:
    render_office_card("rental_agency", office_id)

st.divider()

# ══ 横断で見る ═════════════════════════════════════════════
with st.expander("会社・拠点の一覧（最終接触からの経過が長い順）"):
    render_offices("rental_agency", show_referrals=False)

with st.expander("聞いた話を横断で見る"):
    render_history(["rental_hearing"], "rental_agency",
                   hint="例：美濃加茂 / 外国人 / 空室")

with st.expander("担当者を横断で見る"):
    render_persons("rental_agency")
