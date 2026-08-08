"""分析 — UC-10 紹介元の質、流入の推移

145物件のうち検討値（◎○△）に届いたのは10件で7%。
「どの業者が当たりを持ってくるか」が分かれば、時間の配分を変えられる。
"""
import altair as alt
import pandas as pd
import streamlit as st

from auth import require_password
from db import query
from theme import compact_css, count, ratio

require_password()
compact_css()

st.markdown("### 分析")

# ── 全体像 ──────────────────────────────────────────────────
ov = query("""
    select count(*) as 物件数,
           count(*) filter (where left("cf判定",1) in ('◎','○','△')) as 検討値,
           count(*) filter (where left("cf判定",1) = '×') as 見送り,
           count(*) filter (where "cf判定" is null) as 判定なし
    from re_properties_v where "最新版"
""").iloc[0]

m = st.columns(4)
m[0].metric("物件数（最新版）", f"{ov['物件数']:,}")
m[1].metric("検討値に到達", f"{ov['検討値']:,}",
            f"{ov['検討値'] / max(ov['物件数'], 1) * 100:.1f}%", delta_color="off")
m[2].metric("見送り", f"{ov['見送り']:,}")
m[3].metric("判定なし", f"{ov['判定なし']:,}",
            help="販売価格や年収が未入力、または耐用年数切れで融資不可")

left, right = st.columns(2)

# ── 判定の分布 ──────────────────────────────────────────────
with left:
    st.markdown("#### 判定の分布")
    dist = query("""
        select coalesce(left("cf判定",1),'判定なし') as 判定, count(*) as 件数
        from re_properties_v where "最新版" group by 1
    """)
    order = ["◎", "○", "△", "×", "判定なし"]
    dist["判定"] = pd.Categorical(dist["判定"], categories=order, ordered=True)
    dist = dist.sort_values("判定")
    st.altair_chart(
        alt.Chart(dist).mark_bar().encode(
            x=alt.X("件数:Q"),
            y=alt.Y("判定:N", sort=order),
            color=alt.Color("判定:N", sort=order, legend=None, scale=alt.Scale(
                domain=order,
                range=["#2E9E5B", "#5BB98C", "#D9A404", "#C0392B", "#B0B7C3"])),
            tooltip=["判定", "件数"],
        ).properties(height=180), use_container_width=True)

# ── 月別の流入 ──────────────────────────────────────────────
with right:
    st.markdown("#### 月別の流入")
    flow = query("""
        select to_char("返信日付",'YYYY-MM') as 月, count(*) as 物件数,
               count(*) filter (where left("cf判定",1) in ('◎','○','△')) as 検討値
        from re_properties_v where "返信日付" is not null group by 1 order by 1
    """)
    melted = flow.melt("月", var_name="区分", value_name="件数")
    st.altair_chart(
        alt.Chart(melted).mark_line(point=True).encode(
            x=alt.X("月:N", axis=alt.Axis(labelAngle=-60)),
            y=alt.Y("件数:Q"),
            color=alt.Color("区分:N", scale=alt.Scale(
                domain=["物件数", "検討値"], range=["#3B6FD4", "#2E9E5B"])),
            tooltip=["月", "区分", "件数"],
        ).properties(height=180), use_container_width=True)

# ── 紹介元の質 ──────────────────────────────────────────────
st.markdown("#### 紹介元の質")
st.caption("量ではなく質を見る表です。紹介数が多くても検討値がゼロの先と、"
          "少数でも当てる先が分かれます。")

src = query("""
    select co.name as 紹介元会社, o.branch_name as 拠点,
           count(*) as 紹介数,
           count(*) filter (where left(v."cf判定",1) in ('◎','○','△')) as 検討値,
           round(avg(v."満室利回")*100, 2) as 平均満室利回,
           round(avg(v."積算比率"), 2) as 平均積算比率,
           max(p.reply_date) as 直近の紹介
    from re_properties p
    join re_properties_v v on v.id = p.id
    left join re_offices o on o.id = p.source_office_id
    left join re_companies co on co.id = o.company_id
    where co.name is not null
    group by 1, 2 order by 3 desc, 4 desc
""")
src["当たり率"] = (src["検討値"] / src["紹介数"]).round(3)

c = st.columns([2, 5])
min_n = c[0].number_input("紹介数がこれ以上", value=2, step=1, min_value=1)
shown = src[src["紹介数"] >= min_n]

st.caption(f"{len(shown):,} 社（紹介数 {min_n} 件以上）")
st.dataframe(shown, width="stretch", hide_index=True,
            column_config={
                "紹介数": count("紹介数"),
                "検討値": count("うち◎○△"),
                "当たり率": st.column_config.ProgressColumn(format="percent",
                                                            min_value=0, max_value=1),
                "平均満室利回": st.column_config.NumberColumn(format="%.2f%%"),
                "平均積算比率": st.column_config.NumberColumn(format="%.2f"),
            })

# ── 構造別 ──────────────────────────────────────────────────
st.markdown("#### 構造別")
byst = query("""
    select "構造", "法定耐用年数" as 耐用年数, count(*) as 件数,
           count(*) filter (where left("cf判定",1) in ('◎','○','△')) as 検討値,
           round(avg("満室利回")*100, 2) as 平均満室利回,
           -- 築年数は double precision なので round(x, 桁) を使うには numeric へ変換が要る
           round(avg("築年数")::numeric, 1) as 平均築年数
    from re_properties_v where "最新版"
    group by 1, 2 order by 3 desc
""")
st.dataframe(byst, width="stretch", hide_index=True,
            column_config={
                "件数": count("件数"),
                "検討値": count("うち◎○△"),
                "耐用年数": count("耐用年数", " 年"),
                "平均満室利回": st.column_config.NumberColumn(format="%.2f%%"),
                "平均築年数": st.column_config.NumberColumn(format="%.1f 年"),
            })
