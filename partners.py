"""取引先の画面で共通に使う部品。

DBでは銀行も売買仲介も賃貸仲介も同じ re_companies / re_offices に入れて
`kinds` 配列で種別を持たせている（1社が売買と賃貸を兼ねる実データがあるため）。
一方で見る側は種別ごとに分けたいので、画面はこの部品を kind 違いで呼び分ける。
"""
import pandas as pd
import streamlit as st

from db import execute, query
from nav import goto_property
from theme import count, longtext, money


def office_overview(kind: str) -> pd.DataFrame:
    """指定種別の拠点一覧。紹介実績・接触実績・担当者をまとめて1回で集計する。

    re_properties_v は1行ごとに計算関数を呼ぶため、拠点ごとの相関サブクエリにすると
    拠点数だけビュー全体を評価してしまい実用にならない（実測120秒超）。CTEで1回だけ集計する。
    """
    return query("""
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
        select o.id as office_id, c.name as 会社, o.branch_name as 拠点,
               o.phone as 電話, o.address as 所在地,
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
        where :kind = any(c.kinds)
        order by t.最終接触 nulls last
    """, {"kind": kind})


def render_offices(kind: str, *, show_referrals: bool) -> None:
    """拠点の一覧。最終接触からの経過日数が長い先ほど上に出す。"""
    df = office_overview(kind)
    today = pd.Timestamp.today().normalize()
    df["経過日数"] = (today - pd.to_datetime(df["最終接触"])).dt.days

    c = st.columns([2, 2, 4])
    only_contacted = c[0].toggle("接触実績のある先だけ", value=False, key=f"oc_{kind}")
    also_both = c[1].toggle("兼業の先も表示", value=True, key=f"ob_{kind}",
                            help="売買と賃貸を兼ねている会社など")
    kw = c[2].text_input("会社名・拠点名で検索", key=f"kw_{kind}")

    shown = df.copy()
    if only_contacted:
        shown = shown[shown["接触回数"] > 0]
    if not also_both:
        shown = shown[shown["種別"] == kind]
    if kw:
        shown = shown[shown["会社"].fillna("").str.contains(kw, case=False, na=False)
                      | shown["拠点"].fillna("").str.contains(kw, case=False, na=False)]

    cols = ["会社", "拠点", "担当者", "電話", "最終接触", "経過日数", "接触回数"]
    if show_referrals:
        cols += ["紹介数", "検討値"]

    st.caption(f"{len(shown):,} 拠点"
               + ("" if len(shown) == len(df) else f"（全 {len(df):,} 拠点中）"))
    with st.container(key=f"fulltable_{kind}"):
        st.dataframe(shown[cols], width="stretch", hide_index=True,
                    column_config={
                        "経過日数": count("最終接触からの日数", " 日"),
                        "接触回数": count("接触回数"),
                        "紹介数": count("紹介数"),
                        "検討値": count("うち◎○△", help="紹介物件のうち検討値に届いた件数"),
                    })


def interaction_history(kinds: list[str], company_kind: str) -> pd.DataFrame:
    """接触履歴。物件IDも返すので、行を選んで物件詳細へ飛べる。"""
    return query("""
        select i.id as interaction_id, i.occurred_on as 日付,
               c.name as 会社, o.branch_name as 拠点, o.address as 所在地,
               (select string_agg(coalesce(p.name, ip.person_name_raw), ' / ')
                  from re_interaction_persons ip
                  left join re_persons p on p.id=ip.person_id
                 where ip.interaction_id=i.id) as 相手,
               (select string_agg(coalesce(pr.name, ipp.property_name_raw), ' / ')
                  from re_interaction_properties ipp
                  left join re_properties pr on pr.id=ipp.property_id
                 where ipp.interaction_id=i.id) as 物件,
               (select min(ipp.property_id::text)
                  from re_interaction_properties ipp
                 where ipp.interaction_id=i.id) as property_id,
               i.content as 内容
        from re_interactions i
        join re_offices o on o.id=i.office_id
        join re_companies c on c.id=o.company_id
        where i.kind = any(:kinds) and :ckind = any(c.kinds)
        order by i.occurred_on desc nulls last
    """, {"kinds": kinds, "ckind": company_kind})


def render_history(kinds: list[str], company_kind: str, *, hint: str) -> None:
    """聞いた話の一覧。行を選ぶと、その話に紐づく物件の詳細へ飛ぶ。"""
    hear = interaction_history(kinds, company_kind)

    c = st.columns([3, 3])
    kw = c[0].text_input("内容・エリアの言葉で検索", placeholder=hint,
                         key=f"hk_{company_kind}")
    prop_kw = c[1].text_input("物件名で絞る", key=f"hp_{company_kind}")

    sh = hear.copy()
    if kw:
        sh = sh[sh["内容"].fillna("").str.contains(kw, case=False, na=False)
                | sh["所在地"].fillna("").str.contains(kw, case=False, na=False)
                | sh["拠点"].fillna("").str.contains(kw, case=False, na=False)]
    if prop_kw:
        sh = sh[sh["物件"].fillna("").str.contains(prop_kw, case=False, na=False)]

    st.caption(f"{len(sh):,} 件　—　物件が紐づいている行を選ぶと、その物件の詳細へ移動します")
    ev = st.dataframe(sh[["日付", "会社", "拠点", "相手", "物件", "内容"]],
                     width="stretch", hide_index=True,
                     on_select="rerun", selection_mode="single-row",
                     key=f"hist_{company_kind}",
                     column_config={"内容": longtext("内容")})
    rows = ev.selection.rows
    if rows:
        pid = sh.iloc[rows[0]]["property_id"]
        if pid:
            goto_property(pid)
        else:
            st.info("この記録には物件が紐づいていません。")


def render_persons(company_kind: str) -> None:
    """担当者。異動は前任→後任でつないでいる。"""
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
        where :ckind = any(c.kinds)
        order by c.name, o.branch_name, pe.name
    """, {"ckind": company_kind})

    c = st.columns([2, 4])
    only_cur = c[0].toggle("現任のみ", value=True, key=f"pc_{company_kind}")
    kw = c[1].text_input("氏名・会社名で検索", key=f"pk_{company_kind}")
    sh = ppl[ppl["状態"] == "現任"] if only_cur else ppl
    if kw:
        sh = sh[sh["氏名"].fillna("").str.contains(kw, case=False, na=False)
                | sh["会社"].fillna("").str.contains(kw, case=False, na=False)]
    st.caption(f"{len(sh):,} 名")
    st.dataframe(sh, width="stretch", hide_index=True,
                column_config={"接触回数": count("接触回数")})


def render_add_interaction(company_kind: str, kind_options: dict[str, str]) -> None:
    """接触の記録を追加する。kind_options は {画面の表示名: DBのkind}。"""
    offices = query("""
        select o.id, c.name || '　' || coalesce(o.branch_name,'') as label
        from re_offices o join re_companies c on c.id=o.company_id
        where :ckind = any(c.kinds)
        order by c.name, o.branch_name
    """, {"ckind": company_kind})
    props = query("select id, name from re_properties where name is not null "
                  "order by reply_date desc nulls last")

    with st.form(f"add_{company_kind}"):
        c = st.columns([3, 2, 2])
        off = c[0].selectbox("相手先", offices["label"].tolist())
        kind_label = c[1].selectbox("種別", list(kind_options))
        on = c[2].date_input("日付", value=None)
        rel = st.multiselect("関係する物件（任意）", props["name"].tolist())
        content = st.text_area("内容", height=110)
        ok = st.form_submit_button("記録する", type="primary")

    if ok:
        if not content.strip():
            st.error("内容を入力してください。")
            return
        oid = str(offices.loc[offices["label"] == off, "id"].iloc[0])
        ni = query("""
            insert into re_interactions (office_id, kind, occurred_on, content)
            values (:oid, :k, :on, :content) returning id
        """, {"oid": oid, "k": kind_options[kind_label], "on": on,
              "content": content.strip()})
        iid = str(ni.iloc[0]["id"])
        for nm in rel:
            execute("""
                insert into re_interaction_properties
                  (interaction_id, property_id, property_name_raw)
                values (:iid, :pid, :raw)
            """, {"iid": iid, "pid": str(props.loc[props["name"] == nm, "id"].iloc[0]),
                  "raw": nm})
        query.clear()
        st.success("記録しました。")
        st.rerun()
