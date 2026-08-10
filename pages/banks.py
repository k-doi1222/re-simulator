"""銀行 — UC-7 支店の開拓管理、UC-4 打診結果の横断閲覧

303支店のうち接触済みは27支店しかなく、残りは未活用のリード。
「次にどこへ当たるか」を選べることと、「聞けた融資条件を見比べられること」が目的。

画面は「1支店のカルテ」を主役にしている。支店・担当者・打診の内容は
3つでひとまとまりの情報なので、タブに分けると読みにくく、直すのにも辿り着けない。
横断で見たいもの（支店を探す・打診の結果・担当者一覧）は下に畳んである。
"""
import streamlit as st

from auth import require_password
from db import query
from nav import goto_property, render_back_to_property
from office_card import (render_office_card, render_office_picker,
                         request_office)
from partners import render_persons
from theme import compact_css, count, longtext, money

require_password()
compact_css()

st.markdown("### 銀行")

stat = query("""
    select count(*) as 支店総数,
           count(*) filter (where exists (select 1 from re_interactions i where i.office_id=o.id)) as 接触済,
           count(*) filter (where exists (select 1 from re_bank_loan_terms t where t.office_id=o.id)) as 条件聴取済,
           count(*) filter (where exists (select 1 from re_persons p where p.office_id=o.id)) as 担当者把握
    from re_offices o join re_companies c on c.id=o.company_id
    where 'bank' = any(c.kinds)
""").iloc[0]

m = st.columns(4)
m[0].metric("支店総数", f"{stat['支店総数']:,}")
m[1].metric("接触済み", f"{stat['接触済']:,}",
            f"未接触 {stat['支店総数'] - stat['接触済']:,}", delta_color="off")
m[2].metric("融資条件を聞けた", f"{stat['条件聴取済']:,}")
m[3].metric("担当者を把握", f"{stat['担当者把握']:,}")

# 物件詳細から飛んできたときだけ、戻るボタンを出す
render_back_to_property("bank")

# ══ 支店のカルテ（この画面の主役）══════════════════════════
office_id = render_office_picker("bank")
if office_id:
    render_office_card("bank", office_id)

st.divider()

# ══ 横断で見る ═════════════════════════════════════════════
with st.expander("支店を探す（303支店から絞り込む）"):
    opts = query("""
        select distinct coalesce(bank_category,'(未設定)') as v, 'cat' as k from re_offices
        union all
        select distinct coalesce(region,'(未設定)'), 'reg' from re_offices
    """)
    cats = sorted(opts.loc[opts["k"] == "cat", "v"].tolist())
    regs = sorted(opts.loc[opts["k"] == "reg", "v"].tolist())

    c = st.columns([2, 2, 2, 2])
    f_cat = c[0].multiselect("区分", cats)
    f_reg = c[1].multiselect("地域", regs)
    f_state = c[2].selectbox("状態", ["すべて", "未接触のみ", "接触済みのみ", "融資条件あり"])
    f_kw = c[3].text_input("銀行名・支店名で検索")

    df = query("""
        select office_id,
               "銀行", "支店", "区分", "地域", "電話", "担当者", "打診回数",
               "総合評価", "候補", "融資エリア", "融資期間", "金利", "融資上限",
               "フルローン", "新設法人"
        from re_bank_offices_v
        where (:no_cat or coalesce("区分",'(未設定)') = any(:cats))
          and (:no_reg or coalesce("地域",'(未設定)') = any(:regs))
          and (:kw = '' or "銀行" ilike '%%'||:kw||'%%'
               or coalesce("支店",'') ilike '%%'||:kw||'%%')
          and case :state
                when '未接触のみ'   then "打診回数" = 0
                when '接触済みのみ' then "打診回数" > 0
                when '融資条件あり' then "融資エリア" is not null or "金利" is not null
                else true
              end
        order by "打診回数" desc, "銀行", "支店"
    """, {"no_cat": not f_cat, "cats": f_cat or [""],
          "no_reg": not f_reg, "regs": f_reg or [""],
          "kw": f_kw, "state": f_state})

    st.caption(f"{len(df):,} 支店　—　行を選ぶと、その支店のカルテを開きます")
    ev = st.dataframe(df.drop(columns=["office_id"]), width="stretch", hide_index=True,
                     on_select="rerun", selection_mode="single-row", key="bank_list",
                     column_config={"打診回数": count("打診回数")})
    rows = ev.selection.rows
    if rows:
        request_office("bank", df.iloc[rows[0]]["office_id"])
        st.rerun()

with st.expander("打診の結果を横断で見る"):
    st.caption("1つの物件を複数の銀行へ打診した結果です。"
              "行を選ぶと、その物件の詳細へ移動します。")

    res = query("""
        select p.id as property_id, p.name as 物件, c.name as 銀行, o.branch_name as 支店,
               i.occurred_on as 日付, ip.loanable_amount as 融資可能額,
               ip.result as 結果
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id and i.kind = 'bank_inquiry'
        join re_offices o on o.id = i.office_id
        join re_companies c on c.id = o.company_id
        left join re_properties p on p.id = ip.property_id
        where ip.result is not null
        order by p.name nulls last, c.name
    """)

    c = st.columns([2, 4])
    props = ["すべて"] + sorted(res["物件"].dropna().unique().tolist())
    pick = c[0].selectbox("物件で絞る", props)
    shown = res if pick == "すべて" else res[res["物件"] == pick]

    st.caption(f"{len(shown):,} 件　／　対象物件 {res['物件'].nunique():,} 件")
    ev = st.dataframe(shown[["物件", "銀行", "支店", "日付", "融資可能額", "結果"]],
                     width="stretch", hide_index=True,
                     on_select="rerun", selection_mode="single-row", key="bank_results",
                     column_config={"融資可能額": money("融資可能額"),
                                    "結果": longtext("結果")})
    rows = ev.selection.rows
    if rows:
        pid = shown.iloc[rows[0]]["property_id"]
        if pid:
            goto_property(pid)

    st.info("支店は原文に銀行名しか書かれていなかったため、銀行ごとに接触実績が"
            "最も多い支店を機械的に割り当てています。正確な支店が分かったら直してください。",
            icon=":material/info:")

with st.expander("担当者を横断で見る"):
    render_persons("bank")
