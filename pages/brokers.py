"""仲介業者 — UC-8 関係管理、UC-5 賃貸ヒアリング、UC-9 担当者の異動追随

物件を持ってきてくれる源泉なので、関係を切らさないことが大事。
「そろそろ連絡すべき先」を経過日数で出し、聞いた声はエリア単位でも引けるようにする。
"""
import pandas as pd
import streamlit as st

from auth import require_password
from db import execute, query
from theme import compact_css

require_password()
compact_css()

st.markdown("### 仲介業者")

tab_co, tab_hear, tab_person = st.tabs(["会社・拠点", "聞いた話", "担当者"])

# ══ 会社・拠点 ═════════════════════════════════════════════
with tab_co:
    st.caption("最終接触からの経過日数が長い先ほど上に出ます。紹介数と検討値の実績も並べています。")

    # re_properties_v は1行ごとに計算関数を呼ぶため重い。
    # 拠点ごとの相関サブクエリにすると拠点数だけビュー全体を評価してしまい実用に耐えない
    # （実測で120秒超）。CTEで1回だけ集計してから結合する。
    df = query("""
        with 紹介実績 as (
            select p.source_office_id as office_id,
                   count(*) as 紹介数,
                   count(*) filter (where left(v."cf判定",1) in ('◎','○','△')) as 検討値
            from re_properties p
            join re_properties_v v on v.id = p.id
            where p.source_office_id is not null
            group by 1
        ),
        接触実績 as (
            select office_id, max(occurred_on) as 最終接触, count(*) as 接触回数
            from re_interactions group by 1
        ),
        担当 as (
            select office_id, string_agg(name, ' / ') as 担当者
            from re_persons where coalesce(is_current, true) group by 1
        )
        select c.name as 会社, o.branch_name as 拠点, o.phone as 電話,
               array_to_string(c.kinds, '+') as 種別,
               t.最終接触,
               coalesce(t.接触回数, 0) as 接触回数,
               coalesce(s.紹介数, 0) as 紹介数,
               coalesce(s.検討値, 0) as 検討値,
               d.担当者
        from re_offices o
        join re_companies c on c.id = o.company_id
        left join 紹介実績 s on s.office_id = o.id
        left join 接触実績 t on t.office_id = o.id
        left join 担当     d on d.office_id = o.id
        where 'sales_broker' = any(c.kinds) or 'rental_agency' = any(c.kinds)
        order by t.最終接触 nulls last
    """)
    today = pd.Timestamp.today().normalize()
    df["経過日数"] = (today - pd.to_datetime(df["最終接触"])).dt.days

    c = st.columns([2, 2, 4])
    kinds = c[0].multiselect("種別", ["sales_broker", "rental_agency"])
    only_contacted = c[1].toggle("接触実績のある先だけ", value=False)
    kw = c[2].text_input("会社名・拠点名で検索")

    shown = df.copy()
    if kinds:
        shown = shown[shown["種別"].apply(lambda s: any(k in s for k in kinds))]
    if only_contacted:
        shown = shown[shown["接触回数"] > 0]
    if kw:
        shown = shown[shown["会社"].str.contains(kw, na=False)
                      | shown["拠点"].fillna("").str.contains(kw, na=False)]

    st.caption(f"{len(shown):,} 拠点")
    st.dataframe(
        shown[["会社", "拠点", "種別", "担当者", "電話", "最終接触", "経過日数",
               "接触回数", "紹介数", "検討値"]],
        width="stretch", hide_index=True,
        column_config={
            "経過日数": st.column_config.NumberColumn("最終接触からの日数", format="%d 日"),
            "接触回数": st.column_config.NumberColumn(format="%d"),
            "紹介数": st.column_config.NumberColumn(format="%d"),
            "検討値": st.column_config.NumberColumn("うち◎○△", format="%d",
                                                    help="紹介物件のうち検討値に届いた件数"),
        })

# ══ 聞いた話 ═══════════════════════════════════════════════
with tab_hear:
    st.caption("現地の賃貸仲介店で聞いた声です。物件が変わってもエリアの知見は効くので、"
              "内容の言葉でも検索できます。")

    hear = query("""
        select i.occurred_on as 日付, i.occurred_on_raw as 日付原文,
               c.name as 会社, o.branch_name as 拠点, o.address as 所在地,
               (select string_agg(coalesce(p.name, ip.person_name_raw), ' / ')
                  from re_interaction_persons ip
                  left join re_persons p on p.id=ip.person_id
                 where ip.interaction_id=i.id) as 相手,
               (select string_agg(coalesce(pr.name, ipp.property_name_raw), ' / ')
                  from re_interaction_properties ipp
                  left join re_properties pr on pr.id=ipp.property_id
                 where ipp.interaction_id=i.id) as 物件,
               i.content as 内容
        from re_interactions i
        join re_offices o on o.id=i.office_id
        join re_companies c on c.id=o.company_id
        where i.kind in ('rental_hearing','sales_contact')
        order by i.occurred_on desc nulls last
    """)

    c = st.columns([3, 3])
    kw2 = c[0].text_input("内容・エリアの言葉で検索",
                          placeholder="例：美濃加茂 / 外国人 / 空室")
    prop_kw = c[1].text_input("物件名で絞る")

    sh = hear.copy()
    if kw2:
        mask = (sh["内容"].fillna("").str.contains(kw2, na=False)
                | sh["所在地"].fillna("").str.contains(kw2, na=False)
                | sh["拠点"].fillna("").str.contains(kw2, na=False))
        sh = sh[mask]
    if prop_kw:
        sh = sh[sh["物件"].fillna("").str.contains(prop_kw, na=False)]

    st.caption(f"{len(sh):,} 件")
    st.dataframe(sh[["日付", "会社", "拠点", "相手", "物件", "内容"]],
                width="stretch", hide_index=True,
                column_config={"内容": st.column_config.TextColumn(width="large")})

    with st.expander("聞いた話を記録する"):
        offices = query("""
            select o.id, c.name || '　' || coalesce(o.branch_name,'') as label
            from re_offices o join re_companies c on c.id=o.company_id
            where 'sales_broker' = any(c.kinds) or 'rental_agency' = any(c.kinds)
            order by c.name, o.branch_name
        """)
        props = query("select id, name from re_properties where name is not null "
                      "order by reply_date desc nulls last")
        with st.form("add_hearing"):
            c = st.columns([3, 2, 2])
            off = c[0].selectbox("相手先", offices["label"].tolist())
            kind = c[1].selectbox("種別", ["賃貸ヒアリング", "売買仲介"])
            on = c[2].date_input("日付", value=None)
            rel = st.multiselect("関係する物件（任意）", props["name"].tolist())
            content = st.text_area("内容", height=110,
                                   placeholder="例：外国人を案内すると決まる。駐車場も広い")
            ok = st.form_submit_button("記録する", type="primary")

        if ok:
            if not content.strip():
                st.error("内容を入力してください。")
            else:
                oid = str(offices.loc[offices["label"] == off, "id"].iloc[0])
                k = "rental_hearing" if kind == "賃貸ヒアリング" else "sales_contact"
                ni = query("""
                    insert into re_interactions (office_id, kind, occurred_on, content)
                    values (:oid, :k, :on, :content) returning id
                """, {"oid": oid, "k": k, "on": on, "content": content.strip()})
                iid = str(ni.iloc[0]["id"])
                for nm in rel:
                    execute("""
                        insert into re_interaction_properties
                          (interaction_id, property_id, property_name_raw)
                        values (:iid, :pid, :raw)
                    """, {"iid": iid,
                          "pid": str(props.loc[props["name"] == nm, "id"].iloc[0]),
                          "raw": nm})
                st.success("記録しました。")
                st.rerun()

# ══ 担当者 ═════════════════════════════════════════════════
with tab_person:
    st.caption("異動は前任→後任でつないでいます。現任でない人は「異動済」と表示されます。")
    ppl = query("""
        select pe.name as 氏名, pe.name_kana as かな, pe.role as 役職,
               c.name as 会社, o.branch_name as 拠点,
               pe.phone as 電話, pe.email as メール,
               case when coalesce(pe.is_current,true) then '現任' else '異動済' end as 状態,
               (select s.name from re_persons s where s.id = pe.succeeded_by) as 後任,
               (select count(*) from re_interaction_persons ip
                 where ip.person_id = pe.id) as 接触回数
        from re_persons pe
        join re_offices o on o.id = pe.office_id
        join re_companies c on c.id = o.company_id
        order by c.name, o.branch_name, pe.name
    """)
    c = st.columns([2, 4])
    only_cur = c[0].toggle("現任のみ", value=True)
    kw3 = c[1].text_input("氏名・会社名で検索")
    sh = ppl[ppl["状態"] == "現任"] if only_cur else ppl
    if kw3:
        sh = sh[sh["氏名"].fillna("").str.contains(kw3, na=False)
                | sh["会社"].fillna("").str.contains(kw3, na=False)]
    st.caption(f"{len(sh):,} 名")
    st.dataframe(sh, width="stretch", hide_index=True,
                column_config={"接触回数": st.column_config.NumberColumn(format="%d")})
