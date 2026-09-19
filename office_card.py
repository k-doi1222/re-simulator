"""取引先カルテ — 1つの拠点について「拠点・担当者・やりとり・物件」を1画面にまとめる。

DBは 会社 → 拠点 → 担当者 / やりとり と階層に分けて持っているが、
見るときも直すときも「この支店のこと」がひとまとまりで要る。
タブに分けると同じ相手の情報が散らばって読みにくく、直すのにも辿り着けないため、
種別（銀行・売買仲介・賃貸仲介）によらず同じカルテを使う。

Streamlit はタブを自動で開けないので、物件詳細から飛んできたときに
確実に見せられるよう、カルテはタブの外・ページ本体に置く。
"""
from __future__ import annotations

import re
import uuid

import pandas as pd
import streamlit as st

from db import execute, query
from nav import clear_selection, goto_property, take_office_edit
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
# やりとりの手段（re_interactions.method の check 制約と同じ並び）。
# 残暑見舞い・年賀状は「手紙」。手紙を何回送った相手かを数えるのに使う。
METHODS = ["電話", "面談", "メール", "手紙", "その他"]


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
               o.closed_day as 定休日, o.website as "HP", o.notes as メモ,
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
    info = ([f"所在地：{h['所在地']}"] if h["所在地"] else []) + \
           ([f"定休日：{h['定休日']}"] if h["定休日"] else []) + \
           ([f"HP：[{h['HP']}]({h['HP']})"] if h["HP"] else [])
    if info:
        st.caption("　／　".join(info))
    _summary_memo(h["メモ"])

    _office_info_block(company_kind, office_id, h)
    st.markdown("##### 担当者")
    _persons_block(company_kind, office_id)
    st.markdown(f"##### {KIND_LABEL[ikind]}")
    _interactions_block(company_kind, office_id)
    st.markdown("##### この取引先に関係する物件")
    _properties_block(company_kind, office_id)


# ── 拠点そのものの情報 ──────────────────────────────────────
_EXCEL_ROW = re.compile(r"^元Excel行 *\d+$")


def _summary_memo(notes) -> None:
    """拠点のまとめメモ（re_offices.notes）を読める形で出す。

    人柄・場所・作戦など、日付で変わりにくい情報の置き場所。やりとりの記録とは分けて持つ。
    銀行の「元Excel行 NN」は元データへ遡るための印なので、表示からは外す（DBからは消さない）。
    """
    t = "" if notes is None or (isinstance(notes, float) and pd.isna(notes)) else str(notes)
    body = "\n".join(ln for ln in t.splitlines() if not _EXCEL_ROW.match(ln.strip())).strip()
    if body:
        with st.container(border=True):
            st.caption("まとめメモ　—　直すときは「拠点の情報を直す」から")
            st.markdown(_full(body))


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
            f_web = st.text_input("HP", h["HP"] or "", placeholder="https://")
            f_notes = st.text_area("まとめメモ", h["メモ"] or "", height=150,
                                   help="場所・人柄・作戦など、日付で変わりにくい情報。"
                                        "「元Excel行 NN」は元データへ遡る印なので消さないでください")
            if st.form_submit_button("拠点の情報を保存", type="primary"):
                execute("""
                    update re_offices
                       set branch_name = :b, phone = :p, address = :a,
                           region = :r, bank_category = :cat, closed_day = :cl,
                           website = :w, notes = :n, updated_at = now()
                     where id = cast(:oid as uuid)
                """, {"oid": office_id, "b": _z(f_branch), "p": _z(f_phone),
                      "a": _z(f_addr), "r": _z(f_region), "cat": _z(f_cat),
                      "cl": _z(f_closed), "w": _z(f_web), "n": _z(f_notes)})
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
                 where ip.person_id = pe.id) as 接触回数,
               memo as まとめメモ
        from re_persons pe where office_id = cast(:oid as uuid)
        order by is_current desc, name
    """, {"oid": office_id})


def _persons_block(company_kind: str, office_id: str) -> None:
    cur = persons_of(office_id)
    cur = _blank(cur, ["氏名", "かな", "役職", "電話", "メール", "後任", "まとめメモ"])

    if cur.empty:
        st.caption("まだ登録がありません。下の「担当者を追加」から登録してください。")
    else:
        cols = ["氏名", "かな", "役職", "電話", "メール", "現任", "後任", "接触回数", "まとめメモ"]
        edited = st.data_editor(
            cur[cols], width="stretch", hide_index=True,
            key=f"pe_ed_{office_id}",
            column_config={
                "現任": st.column_config.CheckboxColumn(
                    "現任", help="外すと異動済になります。過去の記録はこの人に残ります"),
                "後任": st.column_config.TextColumn("後任", disabled=True),
                "接触回数": count("接触回数", disabled=True),
                "まとめメモ": st.column_config.TextColumn(
                    "まとめメモ", width="large",
                    help="人柄・勤務形態など、日付で変わりにくいその人の情報。"
                         "やりとりの全文表示で名前の下にも出ます"),
            })
        edit_cols = ["氏名", "かな", "役職", "電話", "メール", "現任", "まとめメモ"]
        changed = _changed(edited[edit_cols], cur[edit_cols])
        n = int(changed.sum())
        if st.button(f"担当者の変更を保存（{n} 名）", type="primary", disabled=(n == 0),
                     key=f"pe_save_{office_id}"):
            for i in edited.index[changed]:
                execute("""
                    update re_persons
                       set name = :name, name_kana = :kana, role = :role,
                           phone = :phone, email = :email, memo = :memo,
                           is_current = :cur, updated_at = now()
                     where id = :id
                """, {"id": str(cur.at[i, "id"]),
                      "name": _z(edited.at[i, "氏名"]), "kana": _z(edited.at[i, "かな"]),
                      "role": _z(edited.at[i, "役職"]), "phone": _z(edited.at[i, "電話"]),
                      "email": _z(edited.at[i, "メール"]),
                      "memo": _z(edited.at[i, "まとめメモ"]),
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
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|>~$<-])")


def _full(text) -> str:
    """長い文章を折り返してそのまま読めるようにする（Markdownとして解釈させない）。"""
    t = "" if text is None or (isinstance(text, float) and pd.isna(text)) else str(text)
    return _MD_SPECIAL.sub(r"\\\1", t.strip()).replace("\n", "  \n")


def _full_cards(hist: pd.DataFrame, memos: dict | None = None) -> None:
    """やりとりを担当者ごとに1枠にまとめ、全文で読める形で並べる。

    人の情報は日付で区切るより続けて読みたいので、日付・手段は各話の末尾に小さく添える。
    DB は1回ずつの記録のまま（いつ聞いた話かを残すため）。表示だけまとめている。
    人についての話（re_interactions.content）だけを出す。物件ごとのメモは
    下の「物件ごとのメモ・結果」に出る。
    複数人のやりとりは「A / B」の組で1枠にする（同じ話を各人に重複させない）。
    memos は 氏名 → 担当者のまとめメモ（re_persons.memo）。名前のすぐ下に出す。
    """
    memos = memos or {}
    # 話が空の回は出さない（名前だけの枠も作らない）。物件メモや表の方で見られる。
    hist = hist[hist["内容"].astype(str).str.strip() != ""]
    # hist は新しい順。枠の並びも「最近話した相手」順になる。
    for who, g in hist.groupby(hist["相手"].replace("", "相手の記録なし"), sort=False):
        with st.container(border=True):
            st.markdown(f"**{_full(who)}**")
            for nm in str(who).split(" / "):
                if memos.get(nm):
                    head = f"{nm}：" if " / " in str(who) else ""
                    st.caption(f"まとめメモ　{_full(head + memos[nm])}")
            for _, r in g.iterrows():
                note = "・".join(x for x in [str(r["日付"]) or "日付なし", str(r["手段"])] if x)
                st.markdown(f"{_full(r['内容'])}  \n:gray[（{_full(note)}）]")


def interactions_of(office_id: str, ikind: str) -> pd.DataFrame:
    return query("""
        select i.id, i.kind, i.occurred_on as 日付, i.method as 手段,
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
        hist = _blank(hist, ["手段", "内容", "相手", "物件"])

        full = st.toggle("全文で表示", value=True, key=f"ix_full_{office_id}",
                         help="オフにすると表になり、日付・手段・内容や物件ごとのメモを直せます")
        if full:
            ppl = persons_of(office_id)
            _full_cards(hist, {r["氏名"]: r["まとめメモ"] for _, r in ppl.iterrows()
                               if isinstance(r["まとめメモ"], str) and r["まとめメモ"].strip()})
            st.caption("文章を直したいときは、上の「全文で表示」をオフにしてください。")
        else:
            # 種別はこのカルテ内で全部同じなので列には出さない（見出しに出ている）。
            # 「どの物件の話か」は手段より先に知りたいので、相手のすぐ隣に置く。
            # 内容が主役なので最後に置き、幅を指定せず残りを全部使わせる。
            cols = ["日付", "相手", "物件", "手段", "内容"]
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
                    "手段": st.column_config.SelectboxColumn("手段", options=METHODS,
                                                              width=80),
                    "内容": st.column_config.TextColumn("内容"),
                })
            edit_cols = ["日付", "手段", "内容"]
            changed = _changed(edited[edit_cols], hist[edit_cols])
            n = int(changed.sum())
            st.caption("日付・手段・内容はこの表で直せます。"
                      "内容を直すと、その内容をそのまま写していた「物件ごとの結果」も一緒に直します。")
            if st.button(f"やりとりの変更を保存（{n} 件）", type="primary", disabled=(n == 0),
                         key=f"ix_save_{office_id}"):
                for i in edited.index[changed]:
                    iid = str(hist.at[i, "id"])
                    new_content = _z(edited.at[i, "内容"])
                    execute("""
                        update re_interactions
                           set occurred_on = :on, method = :method, content = :content
                         where id = cast(:iid as uuid)
                    """, {"iid": iid, "on": _d(edited.at[i, "日付"]),
                          "method": _z(edited.at[i, "手段"]), "content": new_content})
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


        _results_block(office_id, ikind, full)
        _persons_link_block(office_id, hist)

    _add_interaction_block(company_kind, office_id)


def _results_block(office_id: str, ikind: str, full: bool = False) -> None:
    """物件ごとのメモ・結果（re_interaction_properties.result）。1接触×1物件で持つ。

    銀行なら可否・金額、賃貸・売買なら「その物件についての所感」など、
    やりとりのうち物件ごとに分けて残したいこと。融資可能額は銀行のときだけ出す。
    """
    is_bank = ikind == "bank_inquiry"
    res = query("""
        select ip.id, coalesce(pr.name, ip.property_name_raw) as 物件,
               i.occurred_on as 日付, ip.result as メモ結果, ip.loanable_amount as 融資可能額,
               (select string_agg(coalesce(p.name, ipe.person_name_raw), ' / ')
                  from re_interaction_persons ipe
                  left join re_persons p on p.id = ipe.person_id
                 where ipe.interaction_id = i.id) as 相手
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id
        left join re_properties pr on pr.id = ip.property_id
        where i.office_id = cast(:oid as uuid) and i.kind = :ikind
        order by i.occurred_on desc nulls last, 2
    """, {"oid": office_id, "ikind": ikind})
    if res.empty:
        return
    res = _blank(res, ["物件", "メモ結果", "相手"])
    res["日付"] = _dstr(res["日付"])
    res["融資可能額"] = pd.to_numeric(res["融資可能額"], errors="coerce")

    # 物件ごとの記録が主に見たいところなので、最初から開いておく
    with st.expander(f"物件ごとのメモ・結果（{len(res)} 件）", expanded=True):
        st.caption("やりとりのうち物件ごとに分けて残したいこと"
                  "（銀行の可否・金額、業者の物件評価など）。"
                  "物件詳細の「この物件についての結果・メモ」に出ます。")
        if full:
            # やりとりの全文表示と同じ形にする（枠は物件ごとに1つ。
            # 日付・相手・融資可能額は各メモの末尾に灰色で添え、中を日付で区切らない）。
            for prop, g in res.groupby(res["物件"].replace("", "物件の記録なし"), sort=False):
                with st.container(border=True):
                    st.markdown(f"**{_full(prop)}**")
                    for _, r in g.iterrows():
                        amt = r["融資可能額"]
                        note = "・".join(x for x in [
                            str(r["日付"]) or "日付なし", str(r["相手"]),
                            f"融資可能額 {amt:,.0f} 万円" if pd.notna(amt) else ""] if x)
                        memo = str(r["メモ結果"]).strip()
                        st.markdown((f"{_full(memo)}  \n" if memo else "")
                                    + f":gray[（{_full(note)}）]")
            return
        cols = ["物件", "日付", "相手", "メモ結果"] + (["融資可能額"] if is_bank else [])
        conf = {
            "物件": st.column_config.TextColumn("物件", disabled=True, width=200),
            "日付": st.column_config.TextColumn("日付", disabled=True, width=110),
            "相手": st.column_config.TextColumn("相手", disabled=True, width=95),
            "メモ結果": st.column_config.TextColumn("メモ・結果"),
        }
        if is_bank:
            conf["融資可能額"] = money("融資可能額")
        edited = st.data_editor(res[cols], width="stretch", hide_index=True,
                                key=f"rs_ed_{office_id}", column_config=conf)
        edit_cols = ["メモ結果"] + (["融資可能額"] if is_bank else [])
        changed = _changed(edited[edit_cols], res[edit_cols])
        n = int(changed.sum())
        if st.button(f"物件ごとのメモ・結果を保存（{n} 件）", type="primary",
                     disabled=(n == 0), key=f"rs_save_{office_id}"):
            for i in edited.index[changed]:
                amt = _num(edited.at[i, "融資可能額"]) if is_bank \
                    else _num(res.at[i, "融資可能額"])
                execute("""
                    update re_interaction_properties
                       set result = :r, loanable_amount = :amt
                     where id = :id
                """, {"id": str(res.at[i, "id"]), "r": _z(edited.at[i, "メモ結果"]),
                      "amt": amt})
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

        # 保存に成功したら回数を進めて、入力欄をまっさらにする。
        # 残したままだと連打や再実行で同じ記録が二重に入る（実際に発生）。
        # clear_on_submit は入力エラーのときも消えるので使わない。
        gen_key = f"ix_gen_{office_id}"
        gen = st.session_state.get(gen_key, 0)
        fk = f"{office_id}_{gen}"

        with st.form(f"ix_add_{fk}", border=False):
            c = st.columns([2, 2, 4])
            a_on = c[0].date_input("日付", value=None, key=f"ix_on_{fk}")
            a_method = c[1].selectbox("手段", METHODS, index=None, key=f"ix_method_{fk}",
                                      placeholder="選ぶ（任意）")
            a_who = c[2].multiselect("相手", live["氏名"].tolist() if not live.empty else [],
                                     key=f"ix_who_{fk}")
            c = st.columns([5, 2])
            a_props = c[0].multiselect("関係する物件（任意）", props["name"].tolist(),
                                       key=f"ix_props_{fk}")
            a_amt = c[1].number_input("融資可能額（万円・任意）", value=None,
                                      step=100.0, format="%.0f", key=f"ix_amt_{fk}",
                                      help="銀行打診のとき、聞けた金額があれば")
            a_content = st.text_area(
                "全般・担当者の話", height=100, key=f"ix_content_{fk}",
                help="この取引先や担当者についての話（異動、対応の様子、取引姿勢など）")
            a_prop_note = st.text_area(
                "物件についての内容", height=100, key=f"ix_pnote_{fk}",
                help="選んだ物件についての話。複数選んだときは、全部に同じ文が入ります。"
                     "物件ごとに変えたいときは、記録後に「物件ごとのメモ・結果」で直せます")
            if st.form_submit_button("記録する", type="primary"):
                if not a_content.strip() and not a_prop_note.strip():
                    st.error("「全般・担当者の話」か「物件についての内容」のどちらかを入力してください。")
                    return
                if a_prop_note.strip() and not a_props:
                    st.error("「物件についての内容」を入れるときは、関係する物件を選んでください。")
                    return
                iid = str(uuid.uuid4())
                execute("""
                    insert into re_interactions
                      (id, office_id, kind, occurred_on, method, content)
                    values (cast(:id as uuid), cast(:oid as uuid), :k, :on, :method, :content)
                """, {"id": iid, "oid": office_id, "k": kind_db, "on": a_on,
                      "method": a_method, "content": _z(a_content)})
                for nm in a_who:
                    execute("""
                        insert into re_interaction_persons (id, interaction_id, person_id)
                        values (cast(:id as uuid), cast(:iid as uuid), :pid)
                    """, {"id": str(uuid.uuid4()), "iid": iid,
                          "pid": str(live.loc[live["氏名"] == nm, "id"].iloc[0])})
                for nm in a_props:
                    # 物件ごとの結果には「物件についての内容」だけを入れる。
                    # 全般・担当者の話（content）は写さない。
                    execute("""
                        insert into re_interaction_properties
                          (id, interaction_id, property_id, property_name_raw,
                           result, loanable_amount)
                        values (cast(:id as uuid), cast(:iid as uuid), :pid, :raw,
                                :result, :amt)
                    """, {"id": str(uuid.uuid4()), "iid": iid,
                          "pid": str(props.loc[props["name"] == nm, "id"].iloc[0]),
                          "raw": nm, "result": _z(a_prop_note), "amt": a_amt})
                st.session_state[gen_key] = gen + 1
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
        clear_selection(f"rel_{office_id}")
        goto_property(rel.iloc[rows[0]]["id"])
