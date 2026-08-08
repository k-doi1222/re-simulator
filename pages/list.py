"""物件一覧 — UC-1 スクリーニング"""
import streamlit as st

from auth import require_password
from db import query
from nav import goto_property
from theme import CALC_BG, compact_css, count, money, ratio

require_password()  # サイドバー経由の直接遷移で認証をすり抜けないよう、各ページ自身でも確認する
compact_css()

head = st.columns([4, 1])
head[0].markdown("### 物件一覧")
with head[1]:
    if st.button("＋ 物件を登録", width="stretch"):
        st.switch_page("pages/new.py")

# ── 絞り込み ────────────────────────────────────────────────
# re_properties_v は1行ごとに計算関数を呼ぶので、DBからは「最新版かどうか」だけで取得し、
# 判定とキーワードの絞り込みは手元で行う。全体件数と表示件数の両方を出せる。
c1, c2, c3 = st.columns([2, 3, 3])
with c1:
    latest_only = st.toggle("最新版のみ", value=True,
                            help="同じ物件の別バージョンを畳んで、1物件1行で表示します")
with c2:
    marks = st.multiselect("判定で絞る", ["◎", "○", "△", "×", "判定なし"],
                           default=[], help="空のままなら全部表示します")
with c3:
    kw = st.text_input("物件名・所在地で検索", placeholder="例：ビバリーヒルズ / 美濃加茂")

all_df = query("""
    select "物件グループ", id,
           "版", "版数", "最新版", "元excel行", "物件名", "所在地", "構造",
           "築年数", "販売価格", "指値後価格", "満室利回", "積算比率",
           "実質cf", "cf判定", "返信日付", "紹介元会社"
    from re_properties_v
    where (not :latest_only or "最新版")
    order by "実質cf" desc nulls last
""", {"latest_only": latest_only})

df = all_df
if marks:
    mark_of = df["cf判定"].str[0].fillna("判定なし")
    df = df[mark_of.isin(marks)]
if kw:
    df = df[df["物件名"].fillna("").str.contains(kw, case=False, na=False)
            | df["所在地"].fillna("").str.contains(kw, case=False, na=False)]

CALC_COLS = ["築年数", "満室利回", "積算比率", "実質cf", "cf判定"]

filtered = len(df) < len(all_df)
count_text = (f"{len(df):,} 件（全 {len(all_df):,} 件中）" if filtered else f"{len(df):,} 件")
st.caption(f"{count_text}　—　行の左端をクリックすると詳細画面に移動します　"
          "／　青色の列は自動計算された値です（直接編集できません）")

styled = df.style.set_properties(subset=CALC_COLS, **{"background-color": CALC_BG})

# .fulltable の枠に入れると、theme.py の CSS が表の高さを画面下端まで伸ばす
with st.container(key="fulltable"):
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
            "販売価格":   money("販売価格"),
            "指値後価格": money("指値後", help="入力値。詳細画面のスライダーから保存できます"),
            "満室利回":   ratio("満室利回"),
            "積算比率":   ratio("積算比率"),
            "実質cf":     money("実質CF"),
            "cf判定":     st.column_config.TextColumn("判定"),
            "築年数":     count("築年数", " 年"),
            "版数":       count("版数"),
            "最新版":     st.column_config.CheckboxColumn("最新版"),
        },
    )

rows = event.selection.rows
if rows:
    goto_property(df.iloc[rows[0]]["id"])
