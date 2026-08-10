"""物件一覧 — UC-1 スクリーニング

元Excelは行の塗りつぶしで検討状況を表していた。それを「状況」の列にし、
同じ色で行を塗る。状況はこの画面から直接変更できる。
"""
import pandas as pd
import streamlit as st

from auth import require_password
from db import execute, query
from nav import goto_property
from theme import CALC_BG, compact_css, count, longtext, money, ratio

require_password()  # サイドバー経由の直接遷移で認証をすり抜けないよう、各ページ自身でも確認する
compact_css()

head = st.columns([4, 1])
head[0].markdown("### 物件一覧")
with head[1]:
    if st.button("＋ 物件を登録", width="stretch"):
        st.switch_page("pages/new.py")

statuses = query("select status, description, row_color from re_property_statuses "
                 "order by sort_order")
STATUS_LIST = statuses["status"].tolist()
COLOR_OF = dict(zip(statuses["status"], statuses["row_color"]))

# ── 絞り込み ────────────────────────────────────────────────
# re_properties_v は1行ごとに計算関数を呼ぶので、DBからは「最新版かどうか」だけで取得し、
# 状況・判定・キーワードの絞り込みは手元で行う。全体件数と表示件数の両方を出せる。
c1, c2, c3, c4 = st.columns([2, 3, 3, 3])
with c1:
    latest_only = st.toggle("最新版のみ", value=True,
                            help="同じ物件の別バージョンを畳んで、1物件1行で表示します")
with c2:
    f_status = st.multiselect("状況で絞る", STATUS_LIST, default=[],
                              help="空のままなら全部表示します")
with c3:
    marks = st.multiselect("CF基準で絞る", ["◎", "○", "△", "×", "判定なし"], default=[])
with c4:
    kw = st.text_input("物件名・所在地・メモで検索", placeholder="例：ビバリーヒルズ / 美濃加茂")

all_df = query("""
    select "状況", "行の色", "物件グループ", id,
           "版", "版数", "最新版", "元excel行",
           "返信日付", "物件名", "所在地", "cf基準", "到達150", "到達200",
           "築年数", "販売価格", "指値後価格", "積算比率", "メモ", "仲介業者コメント",
           "構造", "満室利回", "実質cf", "紹介元会社"
    from re_properties_v
    where (not :latest_only or "最新版")
    order by "実質cf" desc nulls last
""", {"latest_only": latest_only})

df = all_df
if f_status:
    df = df[df["状況"].isin(f_status)]
if marks:
    mark_of = df["cf基準"].str[0].fillna("判定なし")
    df = df[mark_of.isin(marks)]
if kw:
    df = df[df["物件名"].fillna("").str.contains(kw, case=False, na=False)
            | df["所在地"].fillna("").str.contains(kw, case=False, na=False)
            | df["メモ"].fillna("").str.contains(kw, case=False, na=False)]

# ── 表示用に整える ─────────────────────────────────────────
view = df.copy()
# 価格は「指値後（入力があればそれ、なければ販売価格）」を1列で見せる
view["価格"] = view["指値後価格"].fillna(view["販売価格"])
# メモと業者コメントは1列にまとめる（改行は詰めて1行で読めるように）
view["メモ・コメント"] = (
    view["メモ"].fillna("").str.replace("\n", " ", regex=False) + "  "
    + view["仲介業者コメント"].fillna("").str.replace("\n", " ", regex=False)
).str.strip()

CALC_COLS = ["cf基準", "到達150", "到達200", "築年数", "積算比率"]
COLS = ["状況", "返信日付", "物件名", "所在地", "cf基準", "到達150", "到達200",
        "築年数", "価格", "積算比率", "メモ・コメント"]

filtered = len(df) < len(all_df)
count_text = (f"{len(df):,} 件（全 {len(all_df):,} 件中）" if filtered else f"{len(df):,} 件")
st.caption(f"{count_text}　—　行の左端をクリックすると詳細画面へ　"
          "／　「状況」を選ぶと行の色が変わります　"
          "／　青色の列は自動計算された値です")


def paint(row: pd.Series) -> list[str]:
    """検討状況の色で行を塗る。色がない状況（確認中）は計算値列だけ青くする。"""
    bg = COLOR_OF.get(row["状況"])
    if bg:
        return [f"background-color: {bg}"] * len(row)
    return [f"background-color: {CALC_BG}" if c in CALC_COLS else "" for c in row.index]


styled = view[COLS].style.apply(paint, axis=1)

with st.container(key="fulltable"):
    event = st.dataframe(
        styled,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="property_table",
        column_config={
            "状況":       st.column_config.TextColumn("状況", width="small"),
            "返信日付":   st.column_config.DateColumn("返信日付", format="YYYY-MM-DD"),
            "物件名":     st.column_config.TextColumn("物件名", width="medium"),
            "所在地":     st.column_config.TextColumn("所在地", width="medium"),
            "cf基準":     st.column_config.TextColumn("CF基準", width="small"),
            "到達150":    money("△150にする価格",
                               help="判定を△150に乗せるための指値後価格（基準は販売価格）"),
            "到達200":    money("○200にする価格",
                               help="判定を○200に乗せるための指値後価格（基準は販売価格）"),
            "築年数":     count("築年数", " 年"),
            "価格":       money("価格", help="指値後価格。未入力なら販売価格"),
            "積算比率":   ratio("積算比率"),
            "メモ・コメント": longtext("メモ・コメント"),
        },
    )

rows = event.selection.rows

# ── 選んだ物件の状況を変える ───────────────────────────────
if rows:
    picked = df.iloc[rows[0]]
    st.divider()
    c = st.columns([3, 2, 2])
    c[0].markdown(f"**{picked['物件名']}**")
    cur = picked["状況"] if picked["状況"] in STATUS_LIST else STATUS_LIST[0]
    with c[1]:
        new_status = st.selectbox(
            "状況を変える", STATUS_LIST, index=STATUS_LIST.index(cur),
            key=f"st_{picked['id']}",
            help="　".join(f"{r.status}＝{r.description}" for _, r in statuses.iterrows()))
    with c[2]:
        b = st.columns(2)
        if b[0].button("状況を保存", width="stretch",
                       disabled=(new_status == picked["状況"])):
            execute("update re_properties set status = :s, updated_at = now() where id = :id",
                    {"s": new_status, "id": str(picked["id"])})
            st.session_state.pop("property_table", None)
            st.rerun()
        if b[1].button("詳細へ →", type="primary", width="stretch"):
            goto_property(picked["id"])
