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
    """この種別の拠点の一覧（選択肢用）。やりとりの多い先を上に出す。

    絞り込み用に、会社名・拠点名のほか **担当者名・地域・まとめメモ・紹介物件名** も
    1つの文字列（探す用）にまとめて持つ。銀行は303支店あり、名前だけでは辿りつけない。
    """
    return query("""
        select o.id,
               c.name || '　' || coalesce(o.branch_name, '') as label,
               (select count(*) from re_interactions i
                 where i.office_id = o.id and i.kind = :ikind) as 接触回数,
               lower(concat_ws(' ', c.name, o.branch_name, o.region, o.bank_category,
                      o.address, o.notes,
                      (select string_agg(pe.name, ' ') from re_persons pe
                        where pe.office_id = o.id),
                      (select string_agg(pr.name, ' ') from re_properties pr
                        where pr.source_office_id = o.id))) as 探す用
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

    key = f"card_off_{company_kind}"
    applied = f"{key}_applied"

    # 選択肢を絞る欄。選択肢が増えると、開いて目で探すのが辛くなるため。
    # 会社名だけでなく担当者名・物件名・地域・メモでも当たる。
    q = st.text_input("取引先を探す", key=f"{key}_q",
                      placeholder="会社名・拠点・担当者・地域・物件名・メモで絞り込む").strip()
    shown = offices
    if q:
        keys = [w for w in q.lower().split() if w]
        hit = offices["探す用"].fillna("")
        for w in keys:
            hit = hit.where(offices["探す用"].fillna("").str.contains(w, regex=False), "")
        shown = offices[hit != ""]
        if shown.empty:
            st.warning("見つかりませんでした。別の言葉で探してください。")
            shown = offices
        else:
            st.caption(f"{len(shown):,} 件に絞り込み（全 {len(offices):,} 件）")
            # 今の選択が絞り込みから外れたら、先頭の先へ移す。
            # 選択肢に残すと「探したのに前の先が居座る」形になって紛らわしい。
            cur = st.session_state.get(key)
            if cur and cur not in shown["label"].tolist():
                st.session_state[key] = shown.iloc[0]["label"]

    labels = shown["label"].tolist()

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
            # 絞り込み中に外から指定が来ても、その先を必ず出せるようにする
            if offices.at[hit[0], "label"] not in labels:
                labels = [offices.at[hit[0], "label"]] + labels

    label = st.selectbox("取引先を選ぶ", labels, key=key,
                         help="この欄でも直接入力して絞り込めます")
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

    c = st.columns([5, 1.4], vertical_alignment="bottom")
    c[0].markdown(f"#### {h['会社']}　{h['拠点'] or ''}")
    if c[1].button("＋ やりとりを記録", key=f"addix_{office_id}", width="stretch"):
        add_interaction_dialog(company_kind, office_id)

    # 種別で意味のあるものだけ出す。銀行以外では「区分・地域」が常に空、
    # 賃貸仲介では紹介物件が常に0で、枠が飾りになっていた。
    # 長い値は metric が黙って切るので、全文は help（?）で読めるようにする。
    cells = [("電話", h["電話"] or "—", h["電話"]),
             ("最後のやりとり",
              str(h["最終接触"]) if h["最終接触"] else ("日付なし" if h["接触回数"] else "—"), None),
             (KIND_LABEL[ikind], f"{h['接触回数']:,} 件", None),
             ("担当者", f"{h['担当者数']:,} 名", None)]
    if company_kind != "rental_agency":
        cells.append(("紹介物件", f"{h['紹介数']:,} 件", None))
    if company_kind == "bank":
        cat = "・".join(x for x in [h["区分"], h["地域"]] if x)
        cells.append(("区分・地域", cat or "—", cat))
    m = st.columns(len(cells))
    for col, (lab, val, full) in zip(m, cells):
        col.metric(lab, val, help=full if full and len(str(full)) > 8 else None)
    info = ([f"所在地：{str(h['所在地']).splitlines()[0]}"] if h["所在地"] else []) + \
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
            st.markdown(_full(body))
        # 案内は枠の下に置く。上に置くと見出しのように見えてしまう。
        st.caption("店の性格・場所・作戦など、日付で変わりにくい話。"
                  "直すときは「拠点の情報を直す」から")
    else:
        # 空のときは枠を出さない（中身がある合図が枠なので、空の枠は場所を取るだけ）。
        # ただし1行は残す。やりとりに「拠点メモ参照」と書かれていることがあり、
        # 参照先が画面のどこにも無いと辿れなくなるため。
        st.caption("この拠点のメモはまだありません（「拠点の情報を直す」から書けます）")


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
            # 「元Excel行 NN」は元データへ遡るための印。画面には出さず、DBには残す。
            # 編集欄からも隠し、保存のときに元の行を付け直す（消えないように）。
            keep = [ln for ln in str(h["メモ"] or "").splitlines()
                    if _EXCEL_ROW.match(ln.strip())]
            shown = "\n".join(ln for ln in str(h["メモ"] or "").splitlines()
                               if not _EXCEL_ROW.match(ln.strip())).strip()
            f_notes = st.text_area("まとめメモ", shown, height=150,
                                   help="場所・人柄・作戦など、日付で変わりにくい情報")
            if st.form_submit_button("拠点の情報を保存", type="primary"):
                execute("""
                    update re_offices
                       set branch_name = :b, phone = :p, address = :a,
                           region = :r, bank_category = :cat, closed_day = :cl,
                           website = :w, notes = :n, updated_at = now()
                     where id = cast(:oid as uuid)
                """, {"oid": office_id, "b": _z(f_branch), "p": _z(f_phone),
                      "a": _z(f_addr), "r": _z(f_region), "cat": _z(f_cat),
                      "cl": _z(f_closed), "w": _z(f_web),
                      "n": _z("\n".join(x for x in [f_notes.strip(), *keep] if x))})
                st.toast("保存しました。", icon=":material/check:")
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
                st.toast("保存しました。", icon=":material/check:")
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
        cols = ["氏名", "かな", "役職", "電話", "メール", "現任", "後任", "接触回数"]
        edited = st.data_editor(
            cur[cols], width="stretch", hide_index=True,
            key=f"pe_ed_{office_id}",
            column_config={
                "現任": st.column_config.CheckboxColumn(
                    "現任", help="外すと異動済になります。過去の記録はこの人に残ります"),
                "後任": st.column_config.TextColumn("後任", disabled=True),
                "接触回数": count("やりとり", disabled=True),
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
            st.toast(f"{n} 名を更新しました。", icon=":material/check:")
            st.rerun()

        _person_memo_block(office_id, cur)

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
                if st.form_submit_button("担当者を追加", type="primary"):
                    if not a_name.strip():
                        st.error("氏名を入力してください。")
                    else:
                        _insert_person(office_id, a_name, a_kana, a_role, a_tel, a_mail)
                        st.toast(f"{a_name.strip()} さんを追加しました。", icon=":material/check:")
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
                    if st.form_submit_button("引き継ぎを保存", type="primary"):
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
                            st.toast(f"{old} さん → {s_name.strip()} さんへ引き継ぎました。", icon=":material/check:")
                            st.rerun()


def _person_memo_block(office_id: str, cur: pd.DataFrame) -> None:
    """担当者のまとめメモを直す。やりとりの枠の上段に出るのと同じ文。

    表の中に列として持たせると、1行1セルで改行も入れられず書きにくかったので外に出した。
    """
    with st.expander("担当者のまとめメモを直す"):
        st.caption("人柄・役割・勤務の形など、日付で変わりにくいその人の情報。"
                  "下のやりとりの枠で、名前のすぐ下に出ます。")
        names = cur["氏名"].tolist()
        who = st.selectbox("担当者", names, key=f"pm_who_{office_id}")
        i = cur.index[cur["氏名"] == who][0]
        before = cur.at[i, "まとめメモ"] or ""
        txt = st.text_area("まとめメモ", before, height=120, key=f"pm_txt_{office_id}_{who}")
        if st.button("担当者のまとめメモを保存", type="primary", disabled=(txt == before),
                     key=f"pm_save_{office_id}_{who}"):
            execute("update re_persons set memo = :m, updated_at = now() where id = :id",
                    {"m": _z(txt), "id": str(cur.at[i, "id"])})
            st.toast(f"{who} さんのまとめメモを保存しました。", icon=":material/check:")
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
# `-` は行頭でしか Markdown の意味（箇条書き）を持たない。常にエスケープすると
# 本文中の URL が壊れてリンクが開けなくなる（Evernote の共有リンクで実際に発生）。
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|>~$<])")
_MD_LEAD_DASH = re.compile(r"(?m)^(\s*)-")


def _full(text) -> str:
    """長い文章を折り返してそのまま読めるようにする（Markdownとして解釈させない）。"""
    t = "" if text is None or (isinstance(text, float) and pd.isna(text)) else str(text)
    t = _MD_SPECIAL.sub(r"\\\1", t.strip())
    return _MD_LEAD_DASH.sub(r"\1\\-", t).replace("\n", "  \n")


full_text = _full   # 物件詳細からも同じ整形を使う（改行そのまま・Markdown解釈なし）

_URL = re.compile(r"(https?://[^\s<]+)")
MEMO_FOLD_CHARS = 400   # これより長いまとめメモは畳む
MEMO_HEAD_LINES = 3     # 畳んだときに見せる行数


def memo_block(text: str) -> None:
    """担当者のまとめメモ（第2層）を出す。長いものは先頭だけ見せて畳む。

    人によっては経歴・人柄が数千字になる（狩山さんの例で約2,000字）。
    全部出すと、その下の日付つきの記録まで届かない。
    折りたたみは枠の中に置くので、「人ごとに1枠」の見え方は変わらない。
    st.expander を使うと枠が二重になるため、HTML の details で作る。
    ボタンではないので、開閉しても画面が再描画されない。
    """
    body = (text or "").strip()
    if not body:
        return
    esc = lambda t: (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    fmt = lambda t: _URL.sub(r'<a href="\1" target="_blank">\1</a>',
                             esc(t)).replace("\n", "<br>")
    if len(body) <= MEMO_FOLD_CHARS:
        st.html(f'<div class="memo">{fmt(body)}</div>')
        return
    lines = body.split("\n")
    head, rest = "\n".join(lines[:MEMO_HEAD_LINES]), "\n".join(lines[MEMO_HEAD_LINES:])
    st.html(f'<div class="memo">{fmt(head)}'
            f'<details class="memo-more"><summary></summary>{fmt(rest)}</details></div>')



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
    shown = set()
    # hist は新しい順。枠の並びも「最近話した相手」順になる。
    for who, g in hist.groupby(hist["相手"].replace("", "相手の記録なし"), sort=False):
        shown.update(str(who).split(" / "))
        with st.container(border=True):
            st.markdown(f"**{_full(who)}**")
            # 枠の上段は「今どうなっているか」（担当者のまとめメモ）。
            # 下段は「いつ誰から聞いたか」。区切り線で2つの役割を分ける。
            # 灰色の小さい字にすると読み飛ばされるので、本文と同じ大きさで出す。
            notes = [f"{nm}：{memos[nm]}" if " / " in str(who) else memos[nm]
                     for nm in str(who).split(" / ") if memos.get(nm)]
            # 名札はまとめメモがあるときだけ。未記入の行を毎回出すとうるさい。
            if notes:
                st.caption("この人について")
                memo_block("\n".join(notes))
                st.divider()
                st.caption("やりとり")
            for _, r in g.iterrows():
                note = "・".join(x for x in [str(r["日付"]) or "日付なし", str(r["手段"])] if x)
                st.markdown(_full(r["内容"]))
                edit_popover(f"（{note}）", iid=r["id"], on=r["日付"], method=r["手段"],
                             content=r["内容"])

    # まとめメモはあるが、本文のあるやりとりが無い人。枠が出ないと書いたメモを
    # 本人が二度と読めない（本文を第2層へ移した人がこれに当たる）。
    for nm, memo in memos.items():
        if nm in shown:
            continue
        with st.container(border=True):
            st.markdown(f"**{_full(nm)}**")
            st.caption("この人について")
            memo_block(memo)


def edit_popover(label: str, *, iid, on=None, method=None, content=None,
                 ip_id=None, result=None, amount=None, is_bank: bool = False,
                 shared_note: str = "", key: str = "") -> None:
    """やりとり1件を、読んでいるその場で直す小窓。

    **読む形を既定にして、直すときだけ小窓を開く**という方針の中心部品。
    拠点カルテと物件詳細の両方から呼ぶ（中身が同じなので、2画面で操作が揃う）。
    見た目は日付の注記（灰色の小さい字）のままで、押すと開く。
    別にボタンを置くと、画面が狭いとき（スマホ）に日付と日付の間へ落ちて読みづらい。

    ip_id を渡すと「物件ごとのメモ」も同じ小窓で直せる（銀行なら融資可能額も）。
    """
    k = f"ep_{key or iid}{'_' + str(ip_id) if ip_id else ''}"
    with st.container(key=f"editline_{k}"), st.popover(label, icon=":material/edit:"):
        c = st.columns([3, 2])
        f_on = c[0].text_input("日付", "" if on is None else str(on), key=f"{k}_d",
                               help="2026-08-01 のように入れます。空欄にすると日付なしになります")
        f_m = c[1].selectbox("手段", METHODS, key=f"{k}_m",
                             index=METHODS.index(method) if method in METHODS else None,
                             placeholder="選ぶ（任意）")
        f_c = st.text_area("やりとりの内容（相手先で共通）", "" if content is None else str(content),
                           height=160, key=f"{k}_c",
                           help="この相手と話したこと。物件によらない話はこちら")
        if shared_note and str(content or "").strip():
            # 注意書きは小窓の中だけに出す。読む画面に常時出すと、
            # 本当に危ないときに効かなくなる（空欄にも出ていた）。
            st.caption(f"⚠ この内容は {shared_note} と共通です（直すと両方に反映）")
        f_r = f_amt = None
        if ip_id:
            f_r = st.text_area("物件ごとのメモ", "" if result is None else str(result),
                               height=140, key=f"{k}_r",
                               help="この物件についての話。銀行なら可否や条件")
            if is_bank:
                f_amt = st.number_input("融資可能額（万円）", value=_num(amount),
                                        step=100.0, format="%.0f", key=f"{k}_a")
        if st.button("保存", type="primary", key=f"{k}_s"):
            execute("""
                update re_interactions
                   set occurred_on = :on, method = :method, content = :content
                 where id = cast(:iid as uuid)
            """, {"iid": str(iid), "on": _d(f_on), "method": _z(f_m), "content": _z(f_c)})
            # 移行時に content をそのまま物件側へ写したものだけ追随させる。
            # 個別に直された結果は触らない（この小窓で直した分は次の文で上書きする）。
            execute("""
                update re_interaction_properties set result = :new
                 where interaction_id = cast(:iid as uuid)
                   and result is not distinct from :old
            """, {"iid": str(iid), "new": _z(f_c), "old": _z(content)})
            if ip_id:
                execute("""
                    update re_interaction_properties
                       set result = :r, loanable_amount = :a
                     where id = cast(:ipid as uuid)
                """, {"ipid": str(ip_id), "r": _z(f_r),
                      "a": _num(f_amt) if is_bank else _num(amount)})
            st.toast("保存しました", icon=":material/check:")
            st.rerun()


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
        st.caption("まだ記録がありません。上の「＋ やりとりを記録」から追加できます。")
        return
    hist["種別"] = hist["kind"].map(KIND_LABEL).fillna(hist["kind"])
    hist["日付"] = _dstr(hist["日付"])
    hist = _blank(hist, ["手段", "内容", "相手", "物件"])

    # 読む形を既定にする（直すのは各記録の日付の行から。トグルでの切り替えはやめた）。
    st.markdown("###### 担当者ごと")
    if hist["内容"].astype(str).str.strip().eq("").all():
        st.caption("本文のある記録はまだありません。")
    ppl = persons_of(office_id)
    _full_cards(hist, {r["氏名"]: r["まとめメモ"] for _, r in ppl.iterrows()
                       if isinstance(r["まとめメモ"], str) and r["まとめメモ"].strip()})

    _results_block(office_id, ikind)
    _persons_link_block(office_id, hist)

    # 棚卸しのように一気に直したいときのための表。ふだんは畳んでおく。
    with st.expander("まとめて表で直す"):
        _interactions_table(office_id, hist)
        _results_table(office_id, ikind)


def _interactions_table(office_id: str, hist: pd.DataFrame) -> None:
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
        st.toast(f"{n} 件を更新しました。", icon=":material/check:")
        st.rerun()




def _results_of(office_id: str, ikind: str) -> pd.DataFrame:
    """物件ごとのメモ（re_interaction_properties.result）。1やりとり×1物件で持つ。"""
    res = query("""
        select ip.id, ip.interaction_id, coalesce(pr.name, ip.property_name_raw) as 物件,
               i.occurred_on as 日付, i.method as 手段, i.content as 内容共通,
               ip.result as メモ結果, ip.loanable_amount as 融資可能額,
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
        return res
    res = _blank(res, ["物件", "メモ結果", "相手", "手段", "内容共通"])
    res["日付"] = _dstr(res["日付"])
    res["融資可能額"] = pd.to_numeric(res["融資可能額"], errors="coerce")
    return res


def _results_block(office_id: str, ikind: str) -> None:
    """物件ごとのメモを読む形で出す。直すのは各メモの日付の行から。

    銀行なら可否・金額、賃貸・売買なら「その物件についての所感」など、
    やりとりのうち物件ごとに分けて残したいこと。
    """
    is_bank = ikind == "bank_inquiry"
    res = _results_of(office_id, ikind)
    if res.empty:
        return
    shown = int((res["メモ結果"].astype(str).str.strip() != "").sum())
    # 担当者ごとと同じ見出しの重さで並べる（折りたたみ枠に入れると枠が二重になる）
    st.markdown("###### 物件ごと")
    # 件数は数え方を変えない。「何を数えたか」が分からなくなるため。
    st.caption(f"メモのある {shown} 件（やりとり全 {len(res)} 件）　—　"
               "やりとりのうち物件ごとに分けて残したいこと"
               "（銀行の可否・金額、業者の物件評価など）。物件詳細にも同じものが出ます。")
    # 枠は物件ごとに1つ。中を日付で区切らず、日付・相手・融資可能額は末尾に灰色で添える。
    # 中身が空のものは出さない。全部空の物件は枠ごと出さない。
    live = res[res["メモ結果"].astype(str).str.strip() != ""]
    for prop, g in live.groupby(live["物件"].replace("", "物件の記録なし"), sort=False):
        with st.container(border=True):
            st.markdown(f"**{_full(prop)}**")
            for _, r in g.iterrows():
                amt = r["融資可能額"]
                note = "・".join(x for x in [
                    str(r["日付"]) or "日付なし", str(r["相手"]),
                    f"融資可能額 {amt:,.0f} 万円" if pd.notna(amt) else ""] if x)
                st.markdown(_full(str(r["メモ結果"]).strip()))
                edit_popover(f"（{note}）", iid=r["interaction_id"], ip_id=r["id"],
                             on=r["日付"], method=r["手段"], content=r["内容共通"],
                             result=r["メモ結果"], amount=amt, is_bank=is_bank)


def _results_table(office_id: str, ikind: str) -> None:
    """物件ごとのメモを表でまとめて直す。ふだんは「まとめて表で直す」の中に畳んである。"""
    is_bank = ikind == "bank_inquiry"
    res = _results_of(office_id, ikind)
    if res.empty:
        return
    cols = ["物件", "日付", "相手", "メモ結果"] + (["融資可能額"] if is_bank else [])
    conf = {
        "物件": st.column_config.TextColumn("物件", disabled=True, width=200),
        "日付": st.column_config.TextColumn("日付", disabled=True, width=110),
        "相手": st.column_config.TextColumn("相手", disabled=True, width=95),
        "メモ結果": st.column_config.TextColumn("物件ごとのメモ"),
    }
    if is_bank:
        conf["融資可能額"] = money("融資可能額")
    edited = st.data_editor(res[cols], width="stretch", hide_index=True,
                            key=f"rs_ed_{office_id}", column_config=conf)
    edit_cols = ["メモ結果"] + (["融資可能額"] if is_bank else [])
    changed = _changed(edited[edit_cols], res[edit_cols])
    n = int(changed.sum())
    if st.button(f"物件ごとのメモを保存（{n} 件）", type="primary",
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
        st.toast(f"{n} 件を更新しました。", icon=":material/check:")
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
            st.toast("保存しました。", icon=":material/check:")
            st.rerun()


@st.dialog("やりとりを記録する", width="large")
def add_interaction_dialog(company_kind: str, office_id: str) -> None:
    """記録の入口。**ページの一番下ではなくヘッダから開く。**

    毎日いちばん使う操作なのに、以前は過去ログを全部通り過ぎた先（ページの85%地点）に
    あった。読むためのページを、書くために下まで辿る必要がなくなる。
    """
    _add_interaction_form(company_kind, office_id)


def _add_interaction_form(company_kind: str, office_id: str) -> None:
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
            "やりとりの内容（相手先で共通）", height=100, key=f"ix_content_{fk}",
            help="この取引先や担当者についての話（異動、対応の様子、取引姿勢など）。物件によらない話はこちら")
        a_prop_note = st.text_area(
            "物件ごとのメモ", height=100, key=f"ix_pnote_{fk}",
            help="選んだ物件についての話。複数選んだときは、全部に同じ文が入ります。"
                 "物件ごとに変えたいときは、記録後に「物件ごとのメモ・結果」で直せます")
        if st.form_submit_button("記録する", type="primary"):
            if not a_content.strip() and not a_prop_note.strip():
                st.error("「やりとりの内容」か「物件ごとのメモ」のどちらかを入力してください。")
                return
            if a_prop_note.strip() and not a_props:
                st.error("「物件ごとのメモ」を入れるときは、関係する物件を選んでください。")
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
