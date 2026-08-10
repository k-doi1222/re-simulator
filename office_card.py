"""取引先カルテ — 1つの拠点について「拠点・担当者・やりとり・物件」を1画面にまとめる。

DBは 会社 → 拠点 → 担当者 / やりとり と階層に分けて持っているが、
見るときも直すときも「この支店のこと」がひとまとまりで要る。
タブに分けると同じ相手の情報が散らばって読みにくく、直すのにも辿り着けないため、
種別（銀行・売買仲介・賃貸仲介）によらず同じカルテを使う。

Streamlit はタブを自動で開けないので、物件詳細から飛んできたときに
確実に見せられるよう、カルテはタブの外・ページ本体に置く。
"""
from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st

from db import execute, query
from nav import goto_property, take_office_edit
from theme import count, money

# DBの kind → 画面の言葉
KIND_LABEL = {"bank_inquiry": "銀行打診",
              "rental_hearing": "賃貸ヒアリング",
              "sales_contact": "やりとり"}
# 取引先の種別 → その画面で扱う接触の kind。
# 1社が銀行と売買仲介を兼ねている実データがある（三十三銀行）。絞らないと、
# 売買仲介の画面に銀行打診がずらりと並んでしまう。
KIND_OF = {"bank": "bank_inquiry",
           "rental_agency": "rental_hearing",
           "sales_broker": "sales_contact"}


# ── 小道具 ──────────────────────────────────────────────────
def _z(v) -> str | None:
    """空欄はNULLで保存する。空文字とNULLが混ざると検索や集計がぶれる。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _d(v):
    """表に打ち込まれた日付を date か None にする。'2026/8/1' のような書き方も通す。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s:
        return None
    t = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(t) else t.date()


def _dstr(s: pd.Series) -> pd.Series:
    """日付の列を 'YYYY-MM-DD' か空欄の文字列にする。

    日付のない記録が多い（銀行打診58件のうち42件）。DateColumn に欠損を渡すと
    Streamlit が "None" という文字を描いてしまうので、文字列で扱って空欄に見せる。
    カレンダーは使えなくなるが、打ち込みは _d() が緩く解釈する。
    """
    return (pd.to_datetime(s, errors="coerce")
            .dt.strftime("%Y-%m-%d").fillna(""))


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _changed(edited: pd.DataFrame, before: pd.DataFrame) -> pd.Series:
    """変わった行を返す。

    素の != は NaN != NaN を True にしてしまい、何も触っていないのに
    「変更あり」と判定される（実際にバグらせた）。欠損は空文字に揃えて比べる。
    """
    def norm(df):
        return df.astype(object).where(df.notna(), "")
    return (norm(edited) != norm(before)).any(axis=1)


def _blank(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """値のない欄を空欄にする。渡しっぱなしだと Streamlit が "None" と描く。"""
    for c in cols:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)
    return df


# ── 拠点を選ぶ ──────────────────────────────────────────────
def office_options(company_kind: str) -> pd.DataFrame:
    """この種別の拠点の一覧（選択肢用）。やりとりの多い先を上に出す。"""
    return query("""
        select o.id,
               c.name || '　' || coalesce(o.branch_name, '') as label,
               (select count(*) from re_interactions i
                 where i.office_id = o.id and i.kind = :ikind) as 接触回数
        from re_offices o
        join re_companies c on c.id = o.company_id
        where :ckind = any(c.kinds)
        order by 3 desc, c.name, o.branch_name
    """, {"ckind": company_kind, "ikind": KIND_OF[company_kind]})


def _jump_office(company_kind: str) -> str | None:
    """物件詳細から「この相手先を直す」で来た拠点。この種別のページでだけ効く。"""
    return take_office_edit(company_kind)


def render_office_picker(company_kind: str) -> str | None:
    """拠点を選ぶ欄を出して、選ばれた拠点IDを返す。"""
    offices = office_options(company_kind)
    if offices.empty:
        st.info("拠点がまだ登録されていません。")
        return None

    labels = offices["label"].tolist()
    key = f"card_off_{company_kind}"
    applied = f"{key}_applied"

    # 外から指定された拠点（物件詳細から飛んできた／一覧で行を選んだ）を反映する。
    # 選び直すのは「指定が変わったとき」と「選択状態そのものが無いとき」だけ。
    #   Streamlit は画面を離れるとウィジェットの選択状態を捨てるが、
    #   ここで置くフラグ(applied)は残る。後者を見ないと、2回目に飛んできたときに
    #   選び直しがスキップされて先頭の拠点が出てしまう（実際にそうなった）。
    #   逆に毎回上書きすると、画面上で別の拠点に切り替えられなくなる。
    preset = st.session_state.pop(f"{key}_request", None) or _jump_office(company_kind)
    if preset and (key not in st.session_state
                   or st.session_state.get(applied) != str(preset)):
        hit = offices.index[offices["id"].astype(str) == str(preset)]
        if len(hit):
            st.session_state[key] = offices.at[hit[0], "label"]
            st.session_state[applied] = str(preset)

    label = st.selectbox("取引先を選ぶ", labels, key=key,
                         help="入力すると絞り込めます")
    return str(offices.loc[offices["label"] == label, "id"].iloc[0])


def request_office(company_kind: str, office_id: str) -> None:
    """一覧などから「この拠点を開く」と指示する。次の描画で選択に反映される。"""
    st.session_state[f"card_off_{company_kind}_request"] = str(office_id)


# ── カルテ本体 ──────────────────────────────────────────────
def render_office_card(company_kind: str, office_id: str) -> None:
    """1つの拠点についての全部。見るのも直すのもここで完結させる。"""
    ikind = KIND_OF[company_kind]
    head = query("""
        select c.name as 会社, o.branch_name as 拠点, o.phone as 電話,
               o.address as 所在地, o.region as 地域, o.bank_category as 区分,
               o.closed_day as 定休日, o.notes as メモ,
               (select max(i.occurred_on) from re_interactions i
                 where i.office_id=o.id and i.kind=:ikind) as 最終接触,
               (select count(*) from re_interactions i
                 where i.office_id=o.id and i.kind=:ikind) as 接触回数,
               (select count(*) from re_persons p
                 where p.office_id=o.id and coalesce(p.is_current,true)) as 担当者数,
               (select count(*) from re_properties p where p.source_office_id=o.id) as 紹介数
        from re_offices o join re_companies c on c.id=o.company_id
        where o.id = cast(:oid as uuid)
    """, {"oid": office_id, "ikind": ikind})
    if head.empty:
        st.warning("この拠点は見つかりませんでした。")
        return
    h = head.iloc[0]

    st.markdown(f"#### {h['会社']}　{h['拠点'] or ''}")

    m = st.columns(6)
    m[0].metric("電話", h["電話"] or "—")
    m[1].metric("最終接触", str(h["最終接触"]) if h["最終接触"] else "—")
    m[2].metric(KIND_LABEL[ikind], f"{h['接触回数']:,} 件")
    m[3].metric("担当者", f"{h['担当者数']:,} 名")
    m[4].metric("紹介物件", f"{h['紹介数']:,} 件")
    m[5].metric("区分・地域", "・".join(x for x in [h["区分"], h["地域"]] if x) or "—")
    if h["所在地"]:
        st.caption(f"所在地：{h['所在地']}" + (f"　／　定休日：{h['定休日']}" if h["定休日"] else ""))

    _office_info_block(company_kind, office_id, h)
    st.markdown("##### 担当者")
    _persons_block(company_kind, office_id)
    st.markdown(f"##### {KIND_LABEL[ikind]}")
    _interactions_block(company_kind, office_id)
    st.markdown("##### この取引先に関係する物件")
    _properties_block(company_kind, office_id)


# ── 拠点そのものの情報 ──────────────────────────────────────
def _office_info_block(company_kind: str, office_id: str, h: pd.Series) -> None:
    with st.expander("拠点の情報を直す"):
        with st.form(f"oi_{office_id}", border=False):
            c = st.columns([2, 2, 4])
            f_branch = c[0].text_input("拠点名", h["拠点"] or "")
            f_phone = c[1].text_input("電話", h["電話"] or "")
            f_addr = c[2].text_input("所在地", h["所在地"] or "")
            c = st.columns([2, 2, 2, 2])
            f_region = c[0].text_input("地域", h["地域"] or "")
            f_cat = c[1].text_input("区分", h["区分"] or "")
            f_closed = c[2].text_input("定休日", h["定休日"] or "")
            f_notes = st.text_area("メモ", h["メモ"] or "", height=70,
                                   help="元Excelの行番号などが入っている場合があります。消さないでください")
            if st.form_submit_button("拠点の情報を保存", type="primary"):
                execute("""
                    update re_offices
                       set branch_name = :b, phone = :p, address = :a,
                           region = :r, bank_category = :cat, closed_day = :cl,
                           notes = :n, updated_at = now()
                     where id = cast(:oid as uuid)
                """, {"oid": office_id, "b": _z(f_branch), "p": _z(f_phone),
                      "a": _z(f_addr), "r": _z(f_region), "cat": _z(f_cat),
                      "cl": _z(f_closed), "n": _z(f_notes)})
                st.success("保存しました。")
                st.rerun()

    if company_kind == "bank":
        _loan_terms_block(office_id)


def _loan_terms_block(office_id: str) -> None:
    """銀行の融資条件。1拠点1行（office_id が主キー）なので upsert する。"""
    t = query("""
        select is_candidate, overall_rating, loan_area, loan_term_note,
               interest_rate_note, loan_limit_note, full_loan_note, new_corp_note
        from re_bank_loan_terms where office_id = cast(:oid as uuid)
    """, {"oid": office_id})
    r = t.iloc[0] if not t.empty else pd.Series(dtype=object)

    def v(k):
        return "" if k not in r or pd.isna(r.get(k)) else str(r.get(k))

    with st.expander("融資条件を直す" + ("" if not t.empty else "（まだ未登録）")):
        with st.form(f"lt_{office_id}", border=False):
            c = st.columns([1, 1, 2, 2])
            f_cand = c[0].text_input("候補", v("is_candidate"))
            f_rate = c[1].text_input("総合評価", v("overall_rating"))
            f_area = c[2].text_input("融資エリア", v("loan_area"))
            f_term = c[3].text_input("融資期間", v("loan_term_note"))
            c = st.columns([2, 2, 2, 2])
            f_int = c[0].text_input("金利", v("interest_rate_note"))
            f_lim = c[1].text_input("融資上限", v("loan_limit_note"))
            f_full = c[2].text_input("フルローン", v("full_loan_note"))
            f_corp = c[3].text_input("新設法人", v("new_corp_note"))
            if st.form_submit_button("融資条件を保存", type="primary"):
                execute("""
                    insert into re_bank_loan_terms
                      (office_id, is_candidate, overall_rating, loan_area,
                       loan_term_note, interest_rate_note, loan_limit_note,
                       full_loan_note, new_corp_note, updated_at)
                    values (cast(:oid as uuid), :cand, :rate, :area, :term,
                            :int, :lim, :full, :corp, now())
                    on conflict (office_id) do update set
                      is_candidate = excluded.is_candidate,
                      overall_rating = excluded.overall_rating,
                      loan_area = excluded.loan_area,
                      loan_term_note = excluded.loan_term_note,
                      interest_rate_note = excluded.interest_rate_note,
                      loan_limit_note = excluded.loan_limit_note,
                      full_loan_note = excluded.full_loan_note,
                      new_corp_note = excluded.new_corp_note,
                      updated_at = now()
                """, {"oid": office_id, "cand": _z(f_cand), "rate": _z(f_rate),
                      "area": _z(f_area), "term": _z(f_term), "int": _z(f_int),
                      "lim": _z(f_lim), "full": _z(f_full), "corp": _z(f_corp)})
                st.success("保存しました。")
                st.rerun()


# ── 担当者 ──────────────────────────────────────────────────
def persons_of(office_id: str) -> pd.DataFrame:
    return query("""
        select id, name as 氏名, name_kana as かな, role as 役職,
               phone as 電話, email as メール, is_current as 現任,
               (select s.name from re_persons s where s.id = pe.succeeded_by) as 後任,
               (select count(*) from re_interaction_persons ip
                 where ip.person_id = pe.id) as 接触回数
        from re_persons pe where office_id = cast(:oid as uuid)
        order by is_current desc, name
    """, {"oid": office_id})


def _persons_block(company_kind: str, office_id: str) -> None:
    cur = persons_of(office_id)
    cur = _blank(cur, ["氏名", "かな", "役職", "電話", "メール", "後任"])

    if cur.empty:
        st.caption("まだ登録がありません。下の「担当者を追加」から登録してください。")
    else:
        cols = ["氏名", "かな", "役職", "電話", "メール", "現任", "後任", "接触回数"]
        edited = st.data_editor(
            cur[cols], width="stretch", hide_index=True,
            key=f"pe_ed_{office_id}",
            column_config={
                "現任": st.column_config.CheckboxColumn(
                    "現任", help="外すと異動済になります。過去の記録はこの人に残ります"),
                "後任": st.column_config.TextColumn("後任", disabled=True),
                "接触回数": count("接触回数", disabled=True),
            })
        edit_cols = ["氏名", "かな", "役職", "電話", "メール", "現任"]
        changed = _changed(edited[edit_cols], cur[edit_cols])
        n = int(changed.sum())
        if st.button(f"担当者の変更を保存（{n} 名）", type="primary", disabled=(n == 0),
                     key=f"pe_save_{office_id}"):
            for i in edited.index[changed]:
                execute("""
                    update re_persons
                       set name = :name, name_kana = :kana, role = :role,
                           phone = :phone, email = :email,
                           is_current = :cur, updated_at = now()
                     where id = :id
                """, {"id": str(cur.at[i, "id"]),
                      "name": _z(edited.at[i, "氏名"]), "kana": _z(edited.at[i, "かな"]),
                      "role": _z(edited.at[i, "役職"]), "phone": _z(edited.at[i, "電話"]),
                      "email": _z(edited.at[i, "メール"]),
                      "cur": bool(edited.at[i, "現任"])})
            st.success(f"{n} 名を更新しました。")
            st.rerun()

    c = st.columns(2)
    with c[0]:
        with st.expander("担当者を追加"):
            with st.form(f"pe_add_{office_id}", border=False):
                cc = st.columns([2, 2])
                a_name = cc[0].text_input("氏名")
                a_kana = cc[1].text_input("かな")
                cc = st.columns([2, 2, 3])
                a_role = cc[0].text_input("役職")
                a_tel = cc[1].text_input("電話")
                a_mail = cc[2].text_input("メール")
                if st.form_submit_button("追加する", type="primary"):
                    if not a_name.strip():
                        st.error("氏名を入力してください。")
                    else:
                        _insert_person(office_id, a_name, a_kana, a_role, a_tel, a_mail)
                        st.success(f"{a_name.strip()} さんを追加しました。")
                        st.rerun()

    live = cur[cur["現任"].fillna(True)] if not cur.empty else cur
    with c[1]:
        if not live.empty:
            with st.expander("担当者が代わった（異動の引き継ぎ）"):
                st.caption("前任を異動済にして後任へつなぎます。"
                          "前任と話した過去の記録は前任に残ります。")
                with st.form(f"pe_succ_{office_id}", border=False):
                    cc = st.columns([2, 2])
                    old = cc[0].selectbox("前任", live["氏名"].tolist())
                    s_name = cc[1].text_input("後任の氏名")
                    cc = st.columns([2, 2, 3])
                    s_kana = cc[0].text_input("後任のかな")
                    s_role = cc[1].text_input("後任の役職")
                    s_tel = cc[2].text_input("後任の電話")
                    if st.form_submit_button("引き継ぐ", type="primary"):
                        if not s_name.strip():
                            st.error("後任の氏名を入力してください。")
                        else:
                            new_id = _insert_person(office_id, s_name, s_kana,
                                                    s_role, s_tel, "")
                            execute("""
                                update re_persons
                                   set is_current = false,
                                       succeeded_by = cast(:new as uuid),
                                       updated_at = now()
                                 where id = :old
                            """, {"new": new_id,
                                  "old": str(live.loc[live["氏名"] == old, "id"].iloc[0])})
                            st.success(f"{old} さん → {s_name.strip()} さんへ引き継ぎました。")
                            st.rerun()


def _insert_person(office_id: str, name, kana, role, phone, email) -> str:
    """担当者を1名追加してIDを返す。

    IDは手元で決める。INSERT…RETURNING を使うとキャッシュ付きの query() を
    経由することになり、二重登録の恐れがあるため。
    """
    new_id = str(uuid.uuid4())
    execute("""
        insert into re_persons (id, office_id, name, name_kana, role, phone, email)
        values (cast(:id as uuid), cast(:oid as uuid), :name, :kana, :role, :tel, :mail)
    """, {"id": new_id, "oid": office_id, "name": _z(name), "kana": _z(kana),
          "role": _z(role), "tel": _z(phone), "mail": _z(email)})
    return new_id


# ── やりとり ────────────────────────────────────────────────
def interactions_of(office_id: str, ikind: str) -> pd.DataFrame:
    return query("""
        select i.id, i.kind, i.occurred_on as 日付, i.location as 場所,
               i.content as 内容,
               (select string_agg(coalesce(p.name, ipe.person_name_raw), ' / ')
                  from re_interaction_persons ipe
                  left join re_persons p on p.id = ipe.person_id
                 where ipe.interaction_id = i.id) as 相手,
               (select string_agg(coalesce(pr.name, ipp.property_name_raw), ' / ')
                  from re_interaction_properties ipp
                  left join re_properties pr on pr.id = ipp.property_id
                 where ipp.interaction_id = i.id) as 物件
        from re_interactions i
        where i.office_id = cast(:oid as uuid) and i.kind = :ikind
        order by i.occurred_on desc nulls last, i.created_at
    """, {"oid": office_id, "ikind": ikind})


def _interactions_block(company_kind: str, office_id: str) -> None:
    ikind = KIND_OF[company_kind]
    hist = interactions_of(office_id, ikind)
    if hist.empty:
        st.caption("まだ記録がありません。下の「やりとりを記録する」から追加できます。")
    else:
        hist["種別"] = hist["kind"].map(KIND_LABEL).fillna(hist["kind"])
        hist["日付"] = _dstr(hist["日付"])
        hist = _blank(hist, ["場所", "内容", "相手", "物件"])

        # 種別はこのカルテ内で全部同じなので列には出さない（見出しに出ている）。
        # 「どの物件の話か」は場所より先に知りたいので、相手のすぐ隣に置く。
        # 内容が主役なので最後に置き、幅を指定せず残りを全部使わせる。
        cols = ["日付", "相手", "物件", "場所", "内容"]
        edited = st.data_editor(
            hist[cols], width="stretch", hide_index=True,
            key=f"ix_ed_{office_id}",
            column_config={
                "日付": st.column_config.TextColumn(
                    "日付", width=95,
                    help="2026-08-01 のように入れます。空欄にすると日付なしになります"),
                "相手": st.column_config.TextColumn("相手", disabled=True, width=95,
                                                    help="下の「相手を付け替える」で変えられます"),
                "物件": st.column_config.TextColumn("物件", disabled=True, width=170),
                "場所": st.column_config.TextColumn("場所", width=105),
                "内容": st.column_config.TextColumn("内容"),
            })
        edit_cols = ["日付", "場所", "内容"]
        changed = _changed(edited[edit_cols], hist[edit_cols])
        n = int(changed.sum())
        st.caption("日付・場所・内容はこの表で直せます。"
                  "内容を直すと、その内容をそのまま写していた「物件ごとの結果」も一緒に直します。")
        if st.button(f"やりとりの変更を保存（{n} 件）", type="primary", disabled=(n == 0),
                     key=f"ix_save_{office_id}"):
            for i in edited.index[changed]:
                iid = str(hist.at[i, "id"])
                new_content = _z(edited.at[i, "内容"])
                execute("""
                    update re_interactions
                       set occurred_on = :on, location = :loc, content = :content
                     where id = cast(:iid as uuid)
                """, {"iid": iid, "on": _d(edited.at[i, "日付"]),
                      "loc": _z(edited.at[i, "場所"]), "content": new_content})
                # 銀行打診は移行時に content を物件側の result へそのまま写している。
                # 写しのままのものだけ追随させる（個別に直された結果は触らない）。
                execute("""
                    update re_interaction_properties
                       set result = :new
                     where interaction_id = cast(:iid as uuid)
                       and result is not distinct from :old
                """, {"iid": iid, "new": new_content,
                      "old": _z(hist.at[i, "内容"])})
            st.success(f"{n} 件を更新しました。")
            st.rerun()

        _results_block(office_id, ikind)
        _persons_link_block(office_id, hist)

    _add_interaction_block(company_kind, office_id)


def _results_block(office_id: str, ikind: str) -> None:
    """物件ごとの結果（銀行打診の「◯◯万円まで」など）。1接触×1物件で持っている。"""
    res = query("""
        select ip.id, coalesce(pr.name, ip.property_name_raw) as 物件,
               i.occurred_on as 日付, ip.result as 結果, ip.loanable_amount as 融資可能額
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id
        left join re_properties pr on pr.id = ip.property_id
        where i.office_id = cast(:oid as uuid) and i.kind = :ikind
        order by i.occurred_on desc nulls last, 2
    """, {"oid": office_id, "ikind": ikind})
    if res.empty:
        return
    res = _blank(res, ["物件", "結果"])
    res["日付"] = _dstr(res["日付"])
    res["融資可能額"] = pd.to_numeric(res["融資可能額"], errors="coerce")

    with st.expander(f"物件ごとの結果を直す（{len(res)} 件）"):
        st.caption("同じ相手でも物件ごとに返事が違う場合は、ここで個別に直せます。"
                  "物件詳細の「内容」に出るのはこちらの値です。")
        cols = ["物件", "日付", "結果", "融資可能額"]
        edited = st.data_editor(
            res[cols], width="stretch", hide_index=True,
            key=f"rs_ed_{office_id}",
            column_config={
                "物件": st.column_config.TextColumn("物件", disabled=True, width=200),
                "日付": st.column_config.TextColumn("日付", disabled=True, width=110),
                "結果": st.column_config.TextColumn("結果"),
                "融資可能額": money("融資可能額"),
            })
        edit_cols = ["結果", "融資可能額"]
        changed = _changed(edited[edit_cols], res[edit_cols])
        n = int(changed.sum())
        if st.button(f"物件ごとの結果を保存（{n} 件）", type="primary", disabled=(n == 0),
                     key=f"rs_save_{office_id}"):
            for i in edited.index[changed]:
                execute("""
                    update re_interaction_properties
                       set result = :r, loanable_amount = :amt
                     where id = :id
                """, {"id": str(res.at[i, "id"]), "r": _z(edited.at[i, "結果"]),
                      "amt": _num(edited.at[i, "融資可能額"])})
            st.success(f"{n} 件を更新しました。")
            st.rerun()


def _persons_link_block(office_id: str, hist: pd.DataFrame) -> None:
    """やりとりに「誰と話したか」を付け替える。

    元Excelが支店単位の記録だった銀行打診には、相手が1件も入っていない。
    後から埋められるようにしておく。
    """
    ppl = persons_of(office_id)
    if ppl.empty:
        return
    with st.expander("やりとりの相手を付け替える"):
        opts = hist.index.tolist()

        def lab(i):
            d = str(hist.at[i, "日付"]) or "日付なし"
            return f"{d}　{hist.at[i, '種別']}　{str(hist.at[i, '内容'])[:30]}"

        i = st.selectbox("やりとり", opts, format_func=lab, key=f"pl_pick_{office_id}")
        iid = str(hist.at[i, "id"])
        now = query("""
            select coalesce(p.name, ipe.person_name_raw) as name
            from re_interaction_persons ipe
            left join re_persons p on p.id = ipe.person_id
            where ipe.interaction_id = cast(:iid as uuid)
        """, {"iid": iid})["name"].dropna().tolist()
        names = ppl["氏名"].tolist()
        picked = st.multiselect("相手", names,
                                default=[n for n in now if n in names],
                                key=f"pl_sel_{office_id}_{iid}")
        if st.button("相手を保存", type="primary", key=f"pl_save_{office_id}"):
            execute("delete from re_interaction_persons where interaction_id = cast(:iid as uuid)",
                    {"iid": iid})
            for nm in picked:
                execute("""
                    insert into re_interaction_persons (id, interaction_id, person_id)
                    values (cast(:id as uuid), cast(:iid as uuid), :pid)
                """, {"id": str(uuid.uuid4()), "iid": iid,
                      "pid": str(ppl.loc[ppl["氏名"] == nm, "id"].iloc[0])})
            st.success("保存しました。")
            st.rerun()


def _add_interaction_block(company_kind: str, office_id: str) -> None:
    with st.expander("やりとりを記録する"):
        ppl = persons_of(office_id)
        live = ppl[ppl["現任"].fillna(True)] if not ppl.empty else ppl
        props = query("""
            select id, name from re_properties
            where name is not null order by reply_date desc nulls last
        """)
        # 種別はこの画面のもので固定する。他の種別で記録すると、
        # 保存した直後にこのカルテから消えてしまい、どこへ行ったか分からなくなる。
        kind_db = KIND_OF[company_kind]

        with st.form(f"ix_add_{office_id}", border=False):
            c = st.columns([2, 2, 4])
            a_on = c[0].date_input("日付", value=None)
            a_loc = c[1].text_input("場所")
            a_who = c[2].multiselect("相手", live["氏名"].tolist() if not live.empty else [])
            c = st.columns([5, 2])
            a_props = c[0].multiselect("関係する物件（任意）", props["name"].tolist())
            a_amt = c[1].number_input("融資可能額（万円・任意）", value=None,
                                      step=100.0, format="%.0f",
                                      help="銀行打診のとき、聞けた金額があれば")
            a_content = st.text_area("内容", height=100)
            if st.form_submit_button("記録する", type="primary"):
                if not a_content.strip():
                    st.error("内容を入力してください。")
                    return
                iid = str(uuid.uuid4())
                execute("""
                    insert into re_interactions
                      (id, office_id, kind, occurred_on, location, content)
                    values (cast(:id as uuid), cast(:oid as uuid), :k, :on, :loc, :content)
                """, {"id": iid, "oid": office_id, "k": kind_db, "on": a_on,
                      "loc": _z(a_loc), "content": a_content.strip()})
                for nm in a_who:
                    execute("""
                        insert into re_interaction_persons (id, interaction_id, person_id)
                        values (cast(:id as uuid), cast(:iid as uuid), :pid)
                    """, {"id": str(uuid.uuid4()), "iid": iid,
                          "pid": str(live.loc[live["氏名"] == nm, "id"].iloc[0])})
                for nm in a_props:
                    # 物件ごとの結果には、まず内容をそのまま写しておく。
                    # 物件詳細の「内容」はこの値を先に見るため、写さないと空欄になる。
                    # 物件ごとに返事が違ったら「物件ごとの結果を直す」で個別に直せる。
                    execute("""
                        insert into re_interaction_properties
                          (id, interaction_id, property_id, property_name_raw,
                           result, loanable_amount)
                        values (cast(:id as uuid), cast(:iid as uuid), :pid, :raw,
                                :result, :amt)
                    """, {"id": str(uuid.uuid4()), "iid": iid,
                          "pid": str(props.loc[props["name"] == nm, "id"].iloc[0]),
                          "raw": nm, "result": a_content.strip(), "amt": a_amt})
                st.success("記録しました。")
                st.rerun()


# ── 関係する物件 ────────────────────────────────────────────
def _properties_block(company_kind: str, office_id: str) -> None:
    # 判定値（cf基準など）は re_properties_v から取れるが、あのビューは1行ごとに
    # 計算関数を呼ぶので、拠点カルテを開くたびに待たされる。ここは素の表から引く。
    rel = query("""
        select p.id, p.name as 物件, p.status as 状況, p.purchase_price as 販売価格,
               p.reply_date as 登録日付,
               case when p.source_office_id = cast(:oid as uuid)
                    then '紹介元' else 'やりとりあり' end as 関係
        from re_properties p
        where p.source_office_id = cast(:oid as uuid)
           or exists (
                select 1 from re_interaction_properties ip
                join re_interactions i on i.id = ip.interaction_id
                where ip.property_id = p.id and i.office_id = cast(:oid as uuid))
        order by 6, 5 desc nulls last
    """, {"oid": office_id})
    if rel.empty:
        st.caption("この取引先に紐づく物件はまだありません。")
        return
    rel = _blank(rel, ["物件", "状況"])
    rel["登録日付"] = (pd.to_datetime(rel["登録日付"], errors="coerce")
                       .dt.strftime("%Y-%m-%d").fillna(""))
    st.caption(f"{len(rel):,} 件　—　行を選ぶとその物件の詳細へ移動します")
    ev = st.dataframe(rel[["関係", "登録日付", "物件", "状況", "販売価格"]],
                     width="stretch", hide_index=True,
                     on_select="rerun", selection_mode="single-row",
                     key=f"rel_{office_id}",
                     column_config={"関係": st.column_config.TextColumn("関係", width=110),
                                    "登録日付": st.column_config.TextColumn("登録日付",
                                                                            width=110),
                                    "状況": st.column_config.TextColumn("状況", width=90),
                                    "販売価格": money("販売価格")})
    rows = ev.selection.rows
    if rows:
        goto_property(rel.iloc[rows[0]]["id"])
