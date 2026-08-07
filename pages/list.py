"""物件一覧 — UC-1 スクリーニング"""
import streamlit as st

from auth import require_password
from db import query
from theme import CALC_BG

require_password()  # サイドバー経由の直接遷移で認証をすり抜けないよう、各ページ自身でも確認する

st.title("物件一覧")

# ── 絞り込み ────────────────────────────────────────────────
c1, c2, c3 = st.columns([2, 2, 3])
with c1:
    latest_only = st.toggle("最新版のみ", value=True,
                            help="同じ物件の別バージョンを畳んで、1物件1行で表示します")
with c2:
    marks = st.multiselect("判定", ["◎", "○", "△", "×"], default=["◎", "○", "△"])
with c3:
    kw = st.text_input("物件名・所在地で検索", placeholder="例：ビバリーヒルズ / 美濃加茂")

sql = """
select "物件グループ", id,
       "版", "版数", "最新版", "元excel行", "物件名", "所在地", "構造",
       "築年数", "販売価格", "指値後価格", "満室利回", "積算比率",
       "実質cf", "cf判定", "返信日付", "紹介元会社"
from re_properties_v
where (not :latest_only or "最新版")
  and (:kw = '' or "物件名" ilike '%%' || :kw || '%%'
                or coalesce("所在地",'') ilike '%%' || :kw || '%%')
  and (:no_mark or left("cf判定", 1) = any(:marks))
order by "実質cf" desc nulls last
"""
df = query(sql, {"latest_only": latest_only, "kw": kw,
                 "marks": marks or [""], "no_mark": not marks})

CALC_COLS = ["築年数", "満室利回", "積算比率", "実質cf", "cf判定"]

st.caption(f"{len(df):,} 件　—　行の左端をクリックすると詳細画面に移動します　"
          "／　青色の列は自動計算された値です（直接編集できません）")

styled = df.style.set_properties(subset=CALC_COLS, **{"background-color": CALC_BG})

event = st.dataframe(
    styled,
    width="stretch",
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="property_table",
    # 入力値をまとめ、計算値は後ろにまとめて視覚的にも分ける
    column_order=["版", "版数", "最新版", "元excel行", "物件名", "所在地", "構造",
                 "販売価格", "指値後価格", "返信日付", "紹介元会社",
                 "築年数", "満室利回", "積算比率", "実質cf", "cf判定"],
    column_config={
        "販売価格":   st.column_config.NumberColumn("販売価格", format="%.0f 万円"),
        "指値後価格": st.column_config.NumberColumn("指値後", format="%.0f 万円",
                                                    help="入力値。詳細画面のスライダーから保存できます"),
        "満室利回":   st.column_config.NumberColumn("満室利回", format="percent"),
        "積算比率":   st.column_config.NumberColumn("積算比率", format="%.2f"),
        "実質cf":     st.column_config.NumberColumn("実質CF", format="%.1f 万円"),
        "cf判定":     st.column_config.TextColumn("判定"),
        "築年数":     st.column_config.NumberColumn("築年数", format="%d 年"),
        "版数":       st.column_config.NumberColumn("版数", format="%d"),
        "最新版":     st.column_config.CheckboxColumn("最新版"),
    },
)

rows = event.selection.rows
if rows:
    picked = df.iloc[rows[0]]
    st.session_state["selected_id"] = str(picked["id"])
    st.session_state["selected_group"] = str(picked["物件グループ"])
    st.switch_page("pages/detail.py")
