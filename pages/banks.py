"""銀行 — UC-7 支店の開拓管理、UC-4 打診結果の横断閲覧

303支店のうち接触済みは27支店しかなく、残りは未活用のリード。
「次にどこへ当たるか」を選べることと、「聞けた融資条件を見比べられること」が目的。
"""
import pandas as pd
import streamlit as st

from auth import require_password
from db import execute, query
from theme import CALC_BG, compact_css

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

tab_list, tab_result = st.tabs(["支店を探す", "打診の結果"])

# ══ 支店を探す ═════════════════════════════════════════════
with tab_list:
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
        select "銀行", "支店", "区分", "地域", "電話", "担当者", "打診回数",
               "総合評価", "候補", "融資エリア", "融資期間", "金利", "融資上限",
               "フルローン", "新設法人"
        from re_bank_offices_v
        where (:no_cat or coalesce("区分",'(未設定)') = any(:cats))
          and (:no_reg or coalesce("地域",'(未設定)') = any(:regs))
          and (:kw = '' or "銀行" ilike '%%'||:kw||'%%' or coalesce("支店",'') ilike '%%'||:kw||'%%')
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

    st.caption(f"{len(df):,} 支店")
    st.dataframe(df, width="stretch", hide_index=True,
                column_config={"打診回数": st.column_config.NumberColumn(format="%d")})

# ══ 打診の結果 ═════════════════════════════════════════════
with tab_result:
    st.caption("1つの物件を複数の銀行へ打診した結果です。物件ごとに横断して見比べられます。")

    res = query("""
        select p.name as 物件, c.name as 銀行, o.branch_name as 支店,
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
    st.dataframe(shown, width="stretch", hide_index=True,
                column_config={
                    "融資可能額": st.column_config.NumberColumn(format="%.0f 万円"),
                    "結果": st.column_config.TextColumn(width="large"),
                })

    st.info("支店は原文に銀行名しか書かれていなかったため、銀行ごとに接触実績が"
            "最も多い支店を機械的に割り当てています。正確な支店が分かったら直してください。",
            icon=":material/info:")

    # ── 打診の記録を追加 ────────────────────────────────
    with st.expander("打診の結果を記録する"):
        props_all = query("""
            select id, name, excel_row from re_properties
            where name is not null order by reply_date desc nulls last, excel_row desc
        """)
        banks = query("""
            select o.id, c.name || '　' || coalesce(o.branch_name,'') as label
            from re_offices o join re_companies c on c.id=o.company_id
            where 'bank' = any(c.kinds)
            order by (select count(*) from re_interactions i where i.office_id=o.id) desc,
                     c.name, o.branch_name
        """)
        with st.form("add_inquiry"):
            c = st.columns([3, 3, 2])
            p_label = c[0].selectbox("物件", props_all["name"].tolist())
            b_label = c[1].selectbox("銀行・支店", banks["label"].tolist())
            on = c[2].date_input("打診日", value=None)
            c = st.columns([1, 5])
            amount = c[0].number_input("融資可能額(万円)", value=None, step=100.0)
            result = c[1].text_input("結果", placeholder="例：5000万円が限界／NG／2200万円")
            ok = st.form_submit_button("記録する", type="primary")

        if ok:
            if not result.strip():
                st.error("結果を入力してください。")
            else:
                pid = str(props_all.loc[props_all["name"] == p_label, "id"].iloc[0])
                oid = str(banks.loc[banks["label"] == b_label, "id"].iloc[0])
                new_i = query("""
                    insert into re_interactions (office_id, kind, occurred_on, content)
                    values (:oid, 'bank_inquiry', :on, :content) returning id
                """, {"oid": oid, "on": on, "content": result.strip()})
                execute("""
                    insert into re_interaction_properties
                      (interaction_id, property_id, result, loanable_amount)
                    values (:iid, :pid, :result, :amt)
                """, {"iid": str(new_i.iloc[0]["id"]), "pid": pid,
                      "result": result.strip(), "amt": amount})
                st.success("記録しました。")
                st.rerun()
