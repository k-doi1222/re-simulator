"""取引先の画面で共通に使う部品。

DBでは銀行も売買仲介も賃貸仲介も同じ re_companies / re_offices に入れて
`kinds` 配列で種別を持たせている（1社が売買と賃貸を兼ねる実データがあるため）。
一方で見る側は種別ごとに分けたいので、画面はこの部品を kind 違いで呼び分ける。
"""
import uuid

import pandas as pd
import streamlit as st

from db import execute, query
from nav import goto_property, render_back_to_property, take_office_edit
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
                   count(*) filter (where left(v."cf基準",1) in ('◎','○','△')) as 検討値
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

    # 値のない欄は空欄で見せる（そのまま渡すと Streamlit が "None" と描く）
    for col in ["氏名", "かな", "役職", "会社", "拠点", "電話", "メール", "後任"]:
        ppl[col] = ppl[col].fillna("").astype(str)

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

    # 物件詳細から飛んできたときは画面の上に出しているので、ここでは重ねて出さない
    if not _jump_office(company_kind):
        render_person_editor(company_kind)


def _jump_office(company_kind: str) -> str | None:
    """物件詳細から「この相手先を直す」で来た拠点。この種別のものでなければ None。"""
    oid = take_office_edit()
    if not oid:
        return None
    ok = query("""
        select 1 from re_offices o join re_companies c on c.id = o.company_id
        where o.id = cast(:oid as uuid) and :ckind = any(c.kinds)
    """, {"oid": oid, "ckind": company_kind})
    return oid if not ok.empty else None


def render_office_jump_panel(company_kind: str) -> None:
    """物件詳細から飛んできたときだけ、画面の一番上に編集欄を出す。

    タブの中に置いても Streamlit はタブを自動で開けないので、
    飛んできたときはタブの外に出す。
    """
    oid = _jump_office(company_kind)
    if not oid:
        return
    with st.container(border=True):
        c = st.columns([2, 6])
        with c[0]:
            render_back_to_property()
        c[1].caption("物件詳細から来ています。直し終えたら左のボタンで戻れます。")
        render_person_editor(company_kind, preset_office_id=oid, expanded=True)


def render_person_editor(company_kind: str, preset_office_id: str | None = None,
                         expanded: bool = False) -> None:
    """担当者を直す。

    人を消したり名前を上書きしたりはしない。異動は
    「前任を異動済にして、後任へ succeeded_by でつなぐ」形で残す。
    こうしないと、前任と話した過去の接触記録が後任の記録に化けてしまう。

    preset_office_id を渡すと、その拠点を選んだ状態で開く
    （物件詳細から「この相手先を直す」で飛んできたとき用）。
    """
    with st.expander("担当者を直す（追加・修正・異動）", expanded=expanded):
        offices = query("""
            select o.id, c.name || '　' || coalesce(o.branch_name, '') as label
            from re_offices o
            join re_companies c on c.id = o.company_id
            where :ckind = any(c.kinds)
            order by c.name, o.branch_name
        """, {"ckind": company_kind})
        if offices.empty:
            st.info("拠点がまだ登録されていません。")
            return

        labels = offices["label"].tolist()
        key = f"pe_off_{company_kind}"
        applied = f"{key}_applied"
        # 指定された拠点を選び直すのは次の2つの場合だけ。
        #   (1) 指定が変わったとき
        #   (2) 選択状態そのものが無いとき
        # Streamlit は画面を離れるとウィジェットの選択状態を捨てるが、
        # ここで置くフラグ(applied)は残る。(2)を見ないと、同じ物件から
        # 2回目に飛んできたときに選び直しがスキップされ、先頭の拠点が出てしまう。
        # 逆に毎回上書きすると、画面上で別の拠点に切り替えられなくなる。
        if preset_office_id and (key not in st.session_state
                                 or st.session_state.get(applied) != str(preset_office_id)):
            hit = offices.index[offices["id"].astype(str) == str(preset_office_id)]
            if len(hit):
                st.session_state[key] = offices.at[hit[0], "label"]
                st.session_state[applied] = str(preset_office_id)
        label = st.selectbox("拠点", labels, key=key,
                             help="入力すると絞り込めます")
        oid = str(offices.loc[offices["label"] == label, "id"].iloc[0])

        cur = query("""
            select id, name as 氏名, name_kana as かな, role as 役職,
                   phone as 電話, email as メール, is_current as 現任
            from re_persons where office_id = :oid
            order by is_current desc, name
        """, {"oid": oid})
        for col in ["氏名", "かな", "役職", "電話", "メール"]:
            cur[col] = cur[col].fillna("").astype(str)

        # ── いまいる人を直す ────────────────────────────────
        st.markdown("**この拠点の担当者**")
        if cur.empty:
            st.caption("まだ登録がありません。下の「担当者を追加」から登録してください。")
        else:
            cols = ["氏名", "かな", "役職", "電話", "メール", "現任"]
            edited = st.data_editor(
                cur[cols], width="stretch", hide_index=True,
                key=f"pe_ed_{company_kind}_{oid}",
                column_config={
                    "現任": st.column_config.CheckboxColumn(
                        "現任", help="外すと異動済になります。過去の記録はこの人に残ります"),
                })
            # NaN 同士は「等しい」と見なす。素の != だと NaN != NaN が True になり、
            # 何も触っていないのに「変更あり」と判定されてしまう。
            def _cmp(df):
                return df.astype(object).where(df.notna(), "")

            changed = (_cmp(edited) != _cmp(cur[cols])).any(axis=1)
            n = int(changed.sum())
            if st.button(f"変更を保存（{n} 名）", type="primary", disabled=(n == 0),
                         key=f"pe_save_{company_kind}"):
                for i in edited.index[changed]:
                    execute("""
                        update re_persons
                           set name = :name, name_kana = :kana, role = :role,
                               phone = :phone, email = :email,
                               is_current = :cur, updated_at = now()
                         where id = :id
                    """, {"id": str(cur.at[i, "id"]),
                          "name": _z(edited.at[i, "氏名"]),
                          "kana": _z(edited.at[i, "かな"]),
                          "role": _z(edited.at[i, "役職"]),
                          "phone": _z(edited.at[i, "電話"]),
                          "email": _z(edited.at[i, "メール"]),
                          "cur": bool(edited.at[i, "現任"])})
                st.success(f"{n} 名を更新しました。")
                st.rerun()

        # ── 担当者を追加する ────────────────────────────────
        st.markdown("**担当者を追加**")
        with st.form(f"pe_add_{company_kind}", border=False):
            c = st.columns([2, 2, 2, 2, 3])
            a_name = c[0].text_input("氏名")
            a_kana = c[1].text_input("かな")
            a_role = c[2].text_input("役職")
            a_tel = c[3].text_input("電話")
            a_mail = c[4].text_input("メール")
            if st.form_submit_button("追加する", type="primary"):
                if not a_name.strip():
                    st.error("氏名を入力してください。")
                else:
                    _insert_person(oid, a_name, a_kana, a_role, a_tel, a_mail)
                    st.success(f"{a_name.strip()} さんを追加しました。")
                    st.rerun()

        # ── 異動として引き継ぐ ──────────────────────────────
        live = cur[cur["現任"].fillna(True)] if not cur.empty else cur
        if not live.empty:
            st.markdown("**担当者が代わった（異動の引き継ぎ）**")
            st.caption("前任を異動済にして、後任へつなぎます。"
                      "前任と話した過去の記録は前任に残ります。")
            with st.form(f"pe_succ_{company_kind}", border=False):
                c = st.columns([2, 2, 2, 2, 2])
                old = c[0].selectbox("前任", live["氏名"].tolist())
                s_name = c[1].text_input("後任の氏名")
                s_kana = c[2].text_input("後任のかな")
                s_role = c[3].text_input("後任の役職")
                s_tel = c[4].text_input("後任の電話")
                if st.form_submit_button("引き継ぐ", type="primary"):
                    if not s_name.strip():
                        st.error("後任の氏名を入力してください。")
                    else:
                        new_id = _insert_person(oid, s_name, s_kana, s_role, s_tel, "")
                        execute("""
                            update re_persons
                               set is_current = false, succeeded_by = cast(:new as uuid),
                                   updated_at = now()
                             where id = :old
                        """, {"new": new_id,
                              "old": str(live.loc[live["氏名"] == old, "id"].iloc[0])})
                        st.success(f"{old} さん → {s_name.strip()} さんへ引き継ぎました。")
                        st.rerun()


def _z(v) -> str | None:
    """空欄はNULLで保存する。空文字とNULLが混ざると検索や集計がぶれる。"""
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    return s or None


def _insert_person(office_id: str, name, kana, role, phone, email) -> str:
    """担当者を1名追加してIDを返す。IDは手元で決める（INSERT…RETURNINGは
    キャッシュ付きの query() を経由することになり、二重登録の恐れがあるため）。"""
    new_id = str(uuid.uuid4())
    execute("""
        insert into re_persons (id, office_id, name, name_kana, role, phone, email)
        values (cast(:id as uuid), cast(:oid as uuid), :name, :kana, :role, :tel, :mail)
    """, {"id": new_id, "oid": office_id, "name": _z(name), "kana": _z(kana),
          "role": _z(role), "tel": _z(phone), "mail": _z(email)})
    return new_id


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
