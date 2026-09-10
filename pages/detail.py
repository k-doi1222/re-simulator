"""物件詳細 — UC-2 指値の逆算、UC-3 版の比較、物件情報の編集

1画面で眺めて編集できるよう縦に詰めて並べる。並びは
  シミュレーション → メモ・コメント → 業者とのやりとり → 物件情報（元Excelの列順）
版の比較だけは物件が複数版を持つときにタブで分ける。

「実質CF ÷ 指値後価格」は指値後価格に対して単調に動く（価格を下げるほど良くなる）。
PMT・税金などの計算式そのものはすべてSQL側の re_calc_property_analysis に置いたまま、
2点だけ実際に評価してもらい、そこから「判定値150/200に乗る価格」を代数で解く。
"""
import datetime
import re
import uuid

import pandas as pd
import streamlit as st

from auth import require_password
from db import execute, query, refresh_calc_cache
from nav import goto_office_edit
from theme import CALC_BG, compact_css, count, money, ratio

require_password()  # サイドバー経由の直接遷移で認証をすり抜けないよう、各ページ自身でも確認する
# 一覧に出す計算値は re_property_calc_cache から読む。
# 表示の前に、古くなったものだけ計算し直す（ふだんは0件で一瞬）。
refresh_calc_cache()
compact_css()

RAW_COLS = """
    p.id, p.excel_row, p.name, p.name_raw, p.address, p.structure, p.reply_date,
    p.zoning, p.status, p.source_office_id, p.source_person_id,
    p.memo, p.input_memo, p.broker_comment, p.bank_inquiry_result,
    p.contact_method, p.inquiry_channel,
    p.purchase_price, p.negotiated_price, p.land_area, p.road_price_actual,
    p.zone_coef, p.shape_coef,
    p.floor_area, p.built_date, p.full_income, p.current_income, p.extra_cost,
    p.property_tax,
    p.occupied_units, p.total_units, p.parking_spaces, p.external_parking,
    p.has_elevator, p.has_septic_tank, p.free_internet, p.hazard,
    p.legal_useful_life, p.bank_offered_rate, p.scenario_label,
    coalesce(p.legal_useful_life, re_useful_life_by_structure(p.structure)) as useful_life,
    -- 目標判定に乗せる価格は100万円刻み。丸めた分だけ目標を上回るので、
    -- 減額幅ではなく「その価格でのCF基準」を見せる。
    t150.price as t150_price, t150.discount_rate as t150_rate,
    t150.cf_mark || t150.cf_value::text as t150_cf,
    t200.price as t200_price, t200.discount_rate as t200_rate,
    t200.cf_mark || t200.cf_value::text as t200_cf
"""

# 実質CFは指値後価格に対して完全に線形なので、係数を1回求めれば150も200も出る。
# 目標ごとに re_target_price_detail を呼ぶと、そのたびに2点サンプルを計算し直して
# 1画面で12回も計算していた（実測1.8秒 → 72ms）。
TARGET_JOIN = """
    cross join lateral re_target_price_coef(p.id) k
    cross join lateral re_target_from_coef(k.am, k.kk, k.cc, 150) t150
    cross join lateral re_target_from_coef(k.am, k.kk, k.cc, 200) t200
"""

# 計算関数に渡す入力値。どちらのSQLでも同じ列名で使う。
INPUT_COLS = ["purchase_price", "land_area", "road_price_actual", "zone_coef", "shape_coef",
             "floor_area", "built_date", "full_income", "current_income", "extra_cost",
             "property_tax", "useful_life"]

LIVE_SQL = """
    select * from re_calc_property_analysis(
      :purchase_price, :ar, :land_area, :road_price_actual, :zone_coef, :shape_coef,
      :floor_area, :built_date, :full_income, :current_income, :extra_cost, :property_tax,
      :useful_life, 0.015, 0.09, 0.2, 0.04, 19, current_date, 1, null
    )
"""

# 目標判定に乗せる価格の逆算は、一覧と詳細で同じ結果になるよう
# DB側の re_target_price(物件ID, 目標値) に寄せた（RAW_COLS で取得している）。

UPDATE_SQL = """
    update re_properties set
      reply_date = :reply_date, address = :address, name = :name,
      contact_method = :contact_method, inquiry_channel = :inquiry_channel,
      has_elevator = :has_elevator, has_septic_tank = :has_septic_tank,
      free_internet = :free_internet, hazard = :hazard,
      occupied_units = :occupied_units, total_units = :total_units,
      parking_spaces = :parking_spaces, external_parking = :external_parking,
      structure = :structure, built_date = :built_date,
      purchase_price = :purchase_price, negotiated_price = :negotiated_price,
      road_price_actual = :road_price_actual, property_tax = :property_tax,
      land_area = :land_area, zoning = :zoning,
      zone_coef = :zone_coef, shape_coef = :shape_coef, floor_area = :floor_area,
      full_income = :full_income, current_income = :current_income, extra_cost = :extra_cost,
      bank_offered_rate = :bank_offered_rate, legal_useful_life = :legal_useful_life,
      scenario_label = :scenario_label, input_memo = :input_memo,
      updated_at = now()
    where id = :id
"""

ARINASHI = ["", "あり", "なし"]


def num(v):
    """DataFrameの値を float か None に正規化する。"""
    return None if v is None or pd.isna(v) else float(v)


def txt(v) -> str:
    """DataFrameの値を文字列に正規化する（NULLは空文字）。"""
    return "" if v is None or pd.isna(v) else str(v)


def day(v):
    """DataFrameの値を date か None に正規化する。"""
    if v is None or pd.isna(v):
        return None
    return v if isinstance(v, datetime.date) else pd.to_datetime(v).date()


def blank_to_none(s):
    """空文字はDBのNULLとして保存する。"""
    return None if s is None or str(s).strip() == "" else s


def back_to_list():
    st.session_state.pop("property_table", None)  # 一覧の選択状態をリセット
    st.switch_page("pages/list.py")


# ── 対象物件の読み込み ───────────────────────────────────────
sel_id = st.session_state.get("selected_id")
if not sel_id:
    st.info("物件一覧の行をクリックすると、ここに詳細が表示されます。")
    if st.button("← 物件一覧へ"):
        back_to_list()
    st.stop()

versions = query(f"""
    select {RAW_COLS}, coalesce(p.parent_property_id, p.id) as group_id
    from re_properties p
    {TARGET_JOIN}
    where coalesce(p.parent_property_id, p.id) = (
        select coalesce(parent_property_id, id) from re_properties where id = :id
    )
    order by p.excel_row
""", {"id": sel_id})

if versions.empty:
    st.error("物件が見つかりませんでした。")
    if st.button("← 物件一覧へ"):
        back_to_list()
    st.stop()

# ── ヘッダー（戻る・版選択を1行に収める）────────────────────
head = st.columns([1, 4])
with head[0]:
    if st.button("← 一覧へ", width="stretch"):
        back_to_list()

if len(versions) > 1:
    labels = [f"{txt(r.scenario_label) or '—'}（{r['name']}・行{r.excel_row}）"
              for _, r in versions.iterrows()]
    idx_by_id = {str(r.id): i for i, (_, r) in enumerate(versions.iterrows())}
    with head[1]:
        chosen = st.selectbox("この物件の版", options=range(len(versions)),
                              index=idx_by_id.get(sel_id, 0),
                              format_func=lambda i: labels[i], label_visibility="collapsed")
    prop = versions.iloc[chosen]
    st.session_state["selected_id"] = str(prop["id"])
else:
    prop = versions.iloc[0]

def render_summary():
    """この物件の要点。上から順に「物件のこと」→「値段のこと」→「判定」で並べる。"""
    purchase_price = num(prop["purchase_price"])
    one = lambda s: txt(s).replace("\n", " ").strip()  # noqa: E731  改行を1行に畳む

    # ── 1段目：物件そのもの ───────────────────────────────
    c = st.columns([3.0, 3.2, 1.3])
    c[0].metric("物件名", one(prop["name"]) or "（物件名なし）")
    c[1].metric("所在地", one(prop["address"]) or "—")
    c[2].metric("登録日付", str(prop["reply_date"]) if prop["reply_date"] else "—")

    # ── 2段目：値段。販売価格（元値）のすぐ隣で指値後価格を動かせるようにする ──
    # 入力欄の列は「指値後価格（万円）」のラベルが1行に収まる幅を確保する。
    # 折り返すと入力欄が1行分下がり、隣の保存ボタンと高さがずれる。
    # vertical_alignment="bottom" で、ラベルの有無に関わらず下端を揃える。
    c = st.columns([1.3, 2.1, 0.8, 5.3], vertical_alignment="bottom")
    c[0].metric("販売価格", f"{purchase_price:,.0f} 万円" if purchase_price else "—")
    with c[1]:
        # 整数に見せるのは書式だけ。int のウィジェットにすると保存時に丸めてしまう
        ar = st.number_input(
            "指値後価格（万円）",
            value=float(num(prop["negotiated_price"]) or purchase_price or 0),
            step=10.0, format="%.0f", key=f"ar_{prop['id']}")
    with c[2]:
        if st.button("保存", type="primary", width="stretch"):
            execute("update re_properties set negotiated_price = :ar, updated_at = now() "
                    "where id = :id", {"ar": ar, "id": str(prop["id"])})
            st.success("保存しました")
            st.rerun()

    if not purchase_price:
        st.warning("販売価格が未入力のため、判定を計算できません。下のフォームで入力してください。")
        return None, None

    calc_in = {c: num(prop[c]) if c != "built_date" else day(prop[c]) for c in INPUT_COLS}
    row = query(LIVE_SQL, {**calc_in, "ar": ar}).iloc[0]

    # ── 3段目：判定まわり ─────────────────────────────────
    def discount_pill(price):
        """販売価格からの下げ幅を「↓◯%」で表す。上振れなら向きを反転させる。"""
        rate = (1 - price / purchase_price) * 100
        arrow = "↓" if rate >= 0 else "↑"
        return f"{arrow}{abs(rate):.1f}% 指値"

    def card(label, value, pills=(), sub=False):
        """値＋灰色タグのカード。sub=True は「目安」として控えめに出す。"""
        lab_cls = "tp-lab-sub" if sub else "tp-lab"
        val_cls = "tp-val-sub" if sub else "tp-val"
        pill_cls = "tp-pill tp-pill-sub" if sub else "tp-pill"
        ps = "".join(f'<span class="{pill_cls}">{p}</span>' for p in pills)
        st.html(f'<div class="tp"><div class="{lab_cls}">{label}</div>'
                f'<div class="{val_cls}">{value}</div>'
                f'<div class="tp-pills">{ps}</div></div>')

    def target_card(label, price, rate, cf):
        """目標判定に乗せる価格。100万円刻みに切り下げているので、
        減額幅ではなく「その価格での実際のCF基準」を添える。目安なので控えめに出す。"""
        if price is None or price <= 0:
            card(label, "到達不可", sub=True)
        elif price >= purchase_price:
            card(label, f"{purchase_price:,.0f} 万円", ["現価格で到達"], sub=True)
        else:
            card(label, f"{price:,.0f} 万円",
                 [f"↓{rate:.1f}% 指値", f"CF{cf}"], sub=True)

    # 主役（判定・価格・築年数・積算比率）は同じ大きさで左に、
    # 目安の到達価格は右に控えめに置く。見た目を揃えるため主役も card() で描く。
    # 入居状況は元Excelと同じ「入居数/総戸数」の表記にする。片方だけでも分かるように出す。
    occ, total = num(prop["occupied_units"]), num(prop["total_units"])
    o = f"{occ:.0f}" if occ is not None else "—"
    t = f"{total:.0f}" if total is not None else "—"
    occ_text = "—" if o == "—" and t == "—" else f"{o}/{t}"

    c = st.columns([1.0, 1.35, 0.85, 1.0, 0.9, 0.95, 1.65, 1.65])
    with c[0]:
        card("CF基準", row["c_bu"] or "—")
    with c[1]:
        card("指値後価格", f"{ar:,.0f} 万円", [discount_pill(ar)])
    with c[2]:
        card("築年数", f"{row['c_bb']:.0f} 年" if pd.notna(row["c_bb"]) else "—")
    with c[3]:
        card("満室利回り", f"{row['c_bq'] * 100:.1f}%" if pd.notna(row["c_bq"]) else "—")
    with c[4]:
        card("入居状況", occ_text)
    with c[5]:
        card("積算比率", f"{row['c_bp'] * 100:.0f}%" if pd.notna(row["c_bp"]) else "—")
    with c[6]:
        target_card("△150 にする指値後価格", num(prop["t150_price"]),
                    num(prop["t150_rate"]), txt(prop["t150_cf"]))
    with c[7]:
        target_card("○200 にする指値後価格", num(prop["t200_price"]),
                    num(prop["t200_rate"]), txt(prop["t200_cf"]))

    # 元Excel行は移行してきた物件だけが持つ。この画面から登録した物件は空なので、
    # 「元Excel None行目」と出ないよう、あるときだけ添える。
    origin = (f"　／　元Excel {prop['excel_row']:.0f}行目"
              if pd.notna(prop["excel_row"]) else "")
    st.caption(f"構造 {txt(prop['structure']) or '未設定'}"
              f"・法定耐用年数 {prop['useful_life']:.0f}年" + origin)
    return ar, row


def render_calc_detail(ar, row):
    """判定まわりの計算値。要点に入りきらないものはまとめてここに畳んでおく。

    3つのCFはどれも「収入 − 管理費 − 返済 − 追加費用 − 固都税」で、
    収入の取り方だけが違う。数字の出どころが分かるよう、
    実際に使った値を差し込んだ説明を「?」で出す。
    """
    if row is None:
        return

    def yen(v):
        return f"{v:,.0f} 万円" if pd.notna(v) else "—"

    bd = num(prop["full_income"])          # 満室年収
    be = num(prop["current_income"])       # 現況年収
    bh = num(prop["extra_cost"])           # EV費等の追加
    tax_actual = num(prop["property_tax"])  # 固都税の実額（未入力なら仮計算を使う）
    tax_used = tax_actual if tax_actual is not None else row["c_av"]
    tax_note = ("固都税（実額）" if tax_actual is not None
                else "固都税（実額が未入力のため建物評価から仮計算）")

    common = (
        f"\n\n**共通で差し引くもの**\n"
        f"- 管理費 {yen(row['c_ca'])}"
        f"（満室年収 × 管理費率 {row['c_dg']:.1f}%。管理費率は 9 ＋ 築年数 ÷ 3）\n"
        f"- 年間返済額 {yen(row['c_cd'])}"
        f"（指値後価格の全額を金利1.5%・{row['c_by']:.0f}年で元利均等返済）\n"
        f"- EV費等の追加 {yen(bh) if bh is not None else '0 万円'}\n"
        f"- {tax_note} {yen(tax_used)}")

    with st.expander("計算値の内訳"):
        with st.container(key="calc_block"):
            m = st.columns(4)
            m[0].metric("実質CF", yen(row["c_bt"]), help=(
                "投資判断に使う本命の数字。満室にはならない前提で、"
                "満室年収を92%に割り引いて計算します。\n\n"
                f"満室年収 {yen(bd)} × 92% ＝ {yen(row['c_bz'])} から差し引いて "
                f"**{yen(row['c_bt'])}**" + common))
            m[1].metric("満室時CF", yen(row["c_bs"]), help=(
                "満室が続いた場合のCF。上振れの上限を見る数字です。\n\n"
                f"満室年収 {yen(bd)} から差し引いて **{yen(row['c_bs'])}**" + common))
            m[2].metric("現況CF", yen(row["c_bv"]), help=(
                "今の入居状況のままだった場合のCF。下振れの目安です。\n\n"
                f"現況年収 {yen(be)} から差し引いて **{yen(row['c_bv'])}**" + common))
            m[3].metric("年間返済額", yen(row["c_cd"]), help=(
                f"指値後価格の全額を借りる前提。金利1.5%・融資年数 {row['c_by']:.0f}年"
                "（法定耐用年数 − 築年数、上限30年）の元利均等返済です。"))

        with st.container(key="detail_calc"):
            g = st.columns(6)
            g[0].metric("積算評価", yen(row["c_bo"]),
                        help="土地評価 ＋ 建物評価。銀行が担保として見る価格です。")
            g[1].metric("土地評価", f"{row['c_bm']:,.0f}" if pd.notna(row["c_bm"]) else "—",
                        help="土地面積 × 路線価 × 用途地域係数・土地形状係数の補正")
            g[2].metric("建物評価", f"{row['c_bn']:,.0f}" if pd.notna(row["c_bn"]) else "—",
                        help="単価19 × 延床面積 ×（法定耐用年数 − 築年数）÷ 法定耐用年数")
            g[3].metric("現況利回り",
                        f"{row['c_br'] * 100:.1f}%" if pd.notna(row["c_br"]) else "—",
                        help="現況年収 ÷ 指値後価格")
            g[4].metric("融資年数", f"{row['c_by']:.0f} 年" if pd.notna(row["c_by"]) else "—",
                        help="法定耐用年数 − 築年数（上限30年・下限0年）")
            g[5].metric("返済比率", f"{row['c_cb'] * 100:.0f}%" if pd.notna(row["c_cb"]) else "—",
                        help="年間返済額 ÷ 満室年収")
            g2 = st.columns(6)
            g2[0].metric("購入諸経費", f"{row['c_bw']:,.0f}" if pd.notna(row["c_bw"]) else "—",
                         help="固都税 × 5 ＋ 指値後価格 × 3%")
            g2[1].metric("固都税(仮)", f"{row['c_av']:,.0f}" if pd.notna(row["c_av"]) else "—",
                         help="実額が未入力のときに使う概算。建物評価 × 1.2%")
            g2[2].metric("管理費率", f"{row['c_dg']:.1f}%" if pd.notna(row["c_dg"]) else "—",
                         help="9 ＋ 築年数 ÷ 3。古いほど管理費がかさむ前提")
            g2[3].metric("収益還元評価", f"{row['c_cn']:,.0f}" if pd.notna(row["c_cn"]) else "—",
                         help="満室年収 × 75% ÷ 収益還元率9%")
            g2[4].metric("7年後積算", f"{row['c_dm']:,.0f}" if pd.notna(row["c_dm"]) else "—",
                         help="7年後の土地評価 ＋ 建物評価。出口の目安")
            g2[5].metric("7年通算損益", f"{row['c_dq']:,.0f}" if pd.notna(row["c_dq"]) else "—",
                         help="7年間のCF累計 −（購入価格 − 7年後積算評価）")


def render_memo():
    """メモ・所感・業者コメント。話したことより上に置く。"""
    st.markdown("#### メモ・コメント")

    # 検討状況（元Excelの行の塗りつぶしに相当）。色づけの根拠はメモに書く運用なので隣に置く。
    sts = query("select status, description from re_property_statuses order by sort_order")
    opts = sts["status"].tolist()
    cur = txt(prop["status"]) if txt(prop["status"]) in opts else opts[0]
    c = st.columns([2, 1, 5])
    with c[0]:
        new_st = st.selectbox("検討状況", opts, index=opts.index(cur),
                              key=f"status_{prop['id']}",
                              help="　".join(f"{r.status}＝{r.description}"
                                             for _, r in sts.iterrows()))
    with c[1]:
        st.write("")
        if st.button("状況を保存", width="stretch", disabled=(new_st == cur)):
            execute("update re_properties set status = :s, updated_at = now() where id = :id",
                    {"s": new_st, "id": str(prop["id"])})
            st.rerun()

    with st.form(key=f"memo_{prop['id']}"):
        c = st.columns(2)
        m_memo = c[0].text_area("メモ・所感・疑問", txt(prop["memo"]), height=150)
        m_broker = c[1].text_area("仲介業者コメント", txt(prop["broker_comment"]), height=150)
        ok = st.form_submit_button("メモを保存", type="primary")
    if ok:
        execute("""
            update re_properties set memo = :memo, broker_comment = :broker, updated_at = now()
            where id = :id
        """, {"memo": blank_to_none(m_memo), "broker": blank_to_none(m_broker),
              "id": str(prop["id"])})
        st.success("保存しました。")
        st.rerun()


# ── 業者とのやりとり ────────────────────────────────────────
# 接触の種別 → （取引先の種別, DBの kind）。
# 取引先を種別で絞るのは、1社が銀行と売買仲介を兼ねる実データがあるため
# （絞らないと銀行の選択肢に仲介業者がずらりと混ざる）。
ADD_KINDS = [("銀行打診", "bank", "bank_inquiry"),
             ("賃貸ヒアリング", "rental_agency", "rental_hearing"),
             ("売買仲介とのやりとり", "sales_broker", "sales_contact")]
NEW_ENTRY = "＋ ここに無い先を新しく登録する"


def sales_broker_rows(pid: str) -> pd.DataFrame:
    """この物件の売買仲介を「1行＝1担当者」で集める。紹介元も同じ表に混ぜる。

    DBは2つに分けて持っている。
      紹介元  = re_properties.source_office_id / source_person_id（1物件に1つ）
      やりとり = re_interactions(kind='sales_contact')（何件でも）
    実データでは、両方ある47件のうち実質42件（89%）が同じ相手を指していた。
    **基本形は「紹介元＝やりとり先」**なので、画面では1つの表にまとめて
    どれが紹介元かを印で示す。

    それでも列を分けたまま残しているのは、次の2つが理由。
      1. 紹介元は1つに決まっていないと困る。「紹介元の質」（分析画面）、
         「紹介数・検討値」（取引先一覧）、「関係」（取引先カルテ）の3か所が
         1物件1紹介元を前提に集計している。1物件に売買仲介が3社つく実例があり、
         全部を紹介元に数えると、持ってきていない物件までその業者の実績になる
      2. やりとりの記録が無くても紹介元は分かる。紹介元が入っている144件のうち
         93件はやりとりの記録がゼロ。やりとり側に寄せると出どころが消える
    """
    return query("""
        with ix as (
            select i.office_id, ipe.person_id,
                   count(*) as n, max(i.occurred_on) as last_on,
                   string_agg(coalesce(ip.result, i.content), chr(10)
                              order by i.occurred_on desc nulls last) as body
            from re_interaction_properties ip
            join re_interactions i on i.id = ip.interaction_id
            left join re_interaction_persons ipe on ipe.interaction_id = i.id
            where ip.property_id = cast(:pid as uuid) and i.kind = 'sales_contact'
            group by 1, 2
        ),
        src as (
            select source_office_id as office_id, source_person_id as person_id
            from re_properties
            where id = cast(:pid as uuid) and source_office_id is not null
        ),
        -- union は NULL どうしを同じ値として畳むので、担当者なしの行も重複しない。
        -- 名前を both にしてはいけない（trim(both ...) の予約語で構文エラーになる）
        rel as (
            select office_id, person_id from ix
            union
            select office_id, person_id from src
        )
        select b.office_id, b.person_id, c.kinds,
               (src.office_id is not null) as "紹介元",
               c.name as "会社", o.branch_name as "拠点", pe.name as "担当者",
               coalesce(ix.n, 0) as "やりとり", ix.last_on as "最終接触",
               ix.body as "内容"
        from rel b
        join re_offices o on o.id = b.office_id
        join re_companies c on c.id = o.company_id
        left join re_persons pe on pe.id = b.person_id
        left join ix  on ix.office_id  = b.office_id
                     and ix.person_id  is not distinct from b.person_id
        left join src on src.office_id = b.office_id
                     and src.person_id is not distinct from b.person_id
        order by "紹介元" desc, ix.last_on desc nulls last, c.name
    """, {"pid": pid})


def _office_kind(kinds) -> str:
    """取引先カルテのどの画面へ飛ぶか。1社が複数の顔を持つので優先順で決める。

    紹介元が銀行だった実例がある（メゾン伊賀＝三十三銀行 大垣支店）。
    その会社は売買仲介も兼ねているので売買仲介の画面で開ける。
    """
    ks = list(kinds) if kinds is not None else []
    for k in ("sales_broker", "rental_agency", "bank"):
        if k in ks:
            return k
    return "sales_broker"


def _content_blocks(part: pd.DataFrame) -> None:
    """表のセルに収まらない「内容」を、表の下に全文で出す。

    st.dataframe は1セル1行しか描けず、長い聞き取りメモ（賃貸ヒアリングは
    700〜1100字ある）は畳まれてしまう。読ませたい本文はここで全文を出す。
    """
    for _, r in part.iterrows():
        body = str(r.get("内容", "") or "").strip()
        if not body:
            continue
        head = "　".join(x for x in [str(r.get("日付", "") or ""),
                                     str(r.get("拠点", "") or r.get("会社", "") or ""),
                                     str(r.get("担当者", "") or "")] if x)
        with st.container(border=True):
            if head:
                st.caption(head)
            # 単独の改行も改行として見せる（markdown は行末2スペースで hard break）
            st.markdown(body.replace("\n", "  \n"))


def render_sales_brokers():
    """売買仲介の表。見た目は銀行打診・賃貸ヒアリングと同じ（行を選ぶと取引先カルテへ）。

    紹介元の指定は、この表とは分けて下の別枠（_render_source_picker）で行う。
    分ける理由は sales_broker_rows() の docstring を参照。
    表には★印だけ出して「どれが紹介元か」は分かるようにしておく。
    「内容」は表の下に全文で出す（他の2セクションと同じ扱い）。
    """
    pid = str(prop["id"])
    rows = sales_broker_rows(pid).reset_index(drop=True)

    if not rows.empty:
        view = rows.copy()
        view["最終接触"] = (pd.to_datetime(view["最終接触"], errors="coerce")
                            .dt.strftime("%Y-%m-%d").fillna(""))
        view["印"] = view["紹介元"].map(lambda b: "★" if b else "")
        for col in ["会社", "拠点", "担当者", "内容"]:
            view[col] = view[col].fillna("").astype(str)

        # 値のない列は出さない（他の表と同じ扱い）。売買仲介は拠点名・最終接触が
        # 空のことが多く、空の列があるだけで読みにくくなる。
        base = ["印", "会社", "拠点", "担当者", "やりとり", "最終接触"]
        cols = [c for c in base
                if c in ("印", "会社", "担当者", "やりとり")
                or (view[c].astype(str).str.strip() != "").any()]

        st.caption(f"売買仲介　{len(view)} 件　—　行を選ぶと相手先の担当者を直せます"
                  "（★＝この物件の紹介元）")
        conf = {
            "印":       st.column_config.TextColumn("紹介元", width=55),
            "会社":     st.column_config.TextColumn("会社", width=240),
            "拠点":     st.column_config.TextColumn("拠点", width=220),
            "担当者":   st.column_config.TextColumn("担当者", width=160),
            "やりとり": count("やりとり", " 件"),
            "最終接触": st.column_config.TextColumn("最終接触", width=110),
        }
        ev = st.dataframe(view[cols], width="stretch", hide_index=True,
                          column_config=conf, on_select="rerun",
                          selection_mode="single-row", key=f"sb_{pid}")
        r = ev.selection.rows
        if r:
            row = rows.loc[r[0]]
            goto_office_edit(row["office_id"], _office_kind(row["kinds"]), pid)
        _content_blocks(view)
    else:
        st.caption("売買仲介のやりとりの記録はまだありません。")

    _render_source_picker(pid)


def _render_source_picker(pid: str) -> None:
    """紹介元（この物件を持ってきてくれた業者）を別枠で指定する。

    候補は担当者単位。やりとりの記録が無い相手も選べる
    （紹介元だけ分かっている物件が実データで93件あるため）。
    1社が銀行と売買仲介を兼ねる実例があり（メゾン伊賀＝三十三銀行 大垣支店）、
    そういう会社も候補に出る。
    """
    cur_office = txt(prop["source_office_id"])
    cur_person = txt(prop["source_person_id"])

    cand = query("""
        select o.id::text as office_id, pe.id::text as person_id,
               c.name || '　' || coalesce(o.branch_name, '')
                 || case when pe.name is not null then '　' || pe.name
                         else '　（担当者未指定）' end as label
        from re_offices o
        join re_companies c on c.id = o.company_id
        left join re_persons pe on pe.office_id = o.id
             and (coalesce(pe.is_current, true)
                  or pe.id = cast(nullif(:cur_person, '') as uuid))
        where 'sales_broker' = any(c.kinds) or 'rental_agency' = any(c.kinds)
        order by c.name, o.branch_name nulls first, pe.name nulls first
    """, {"cur_person": cur_person})

    NONE = "（未設定）"
    opts = [NONE] + cand["label"].tolist()

    cur_label = NONE
    if cur_office:
        m = cand[(cand["office_id"] == cur_office)
                 & (cand["person_id"].fillna("") == cur_person)]
        if not m.empty:
            cur_label = m.iloc[0]["label"]

    st.markdown("**紹介元（この物件を持ってきてくれた業者）**")
    c = st.columns([6, 1], vertical_alignment="bottom")
    sel = c[0].selectbox(
        "紹介元にする担当者", opts, index=opts.index(cur_label),
        key=f"src_{pid}", label_visibility="collapsed",
        help="この物件情報の出どころ。1つだけ。やりとりの記録が無い相手も選べます")
    if c[1].button("保存", key=f"src_save_{pid}", disabled=(sel == cur_label)):
        if sel == NONE:
            set_source(pid, None, None)
        else:
            hit = cand[cand["label"] == sel].iloc[0]
            set_source(pid, hit["office_id"],
                       hit["person_id"] if pd.notna(hit["person_id"]) else None)
        st.rerun()


def set_source(pid: str, office_id: str | None, person_id: str | None) -> None:
    """紹介元を差し替える。拠点と担当者は必ず一緒に動かす（片方だけ残すと食い違う）。"""
    execute("""
        update re_properties
           set source_office_id = cast(:o as uuid),
               source_person_id = cast(:p as uuid),
               updated_at = now()
         where id = cast(:id as uuid)
    """, {"o": office_id, "p": person_id, "id": pid})


def render_add_interaction():
    """この画面から、やりとり・打診を手で足す。

    会社・拠点・担当者もその場で作れるようにしてある。取引先を新規登録する画面が
    アプリのどこにも無く、未登録の相手については何も記録できなかったため。

    種別・会社・拠点の選択は **フォームの外** に置く。st.form の中の選択は
    送信するまで反映されないので、「会社を選ぶ→その会社の拠点が出る」が動かない。
    """
    pid = str(prop["id"])
    with st.expander("＋ やりとり・打診を記録する"):
        c = st.columns([2, 3, 3])
        label = c[0].selectbox("種別", [k[0] for k in ADD_KINDS], key=f"ak_{pid}")
        _, ckind, kind_db = next(k for k in ADD_KINDS if k[0] == label)

        comps = query("select c.id, c.name from re_companies c "
                      "where :ckind = any(c.kinds) order by c.name", {"ckind": ckind})
        # 「新しく登録する」は選択肢の**先頭**に置く。末尾だと会社が100件以上あって
        # スクロールしないと見えず、あることに気づけない。初期選択は先頭の会社にする。
        # キーに種別・会社を混ぜているのは、選択肢が総入れ替えになったときに
        # 前の選択が残っていると Streamlit が「選択肢に無い」で落ちるため。
        comp_opts = [NEW_ENTRY] + comps["name"].tolist()
        comp = c[1].selectbox("会社", comp_opts, index=min(1, len(comp_opts) - 1),
                              key=f"ac_{pid}_{ckind}")
        new_company = comp == NEW_ENTRY

        comp_id, office_id = None, None
        if new_company:
            c[2].caption("会社名・拠点名は下の欄に入れてください。")
        else:
            comp_id = str(comps.loc[comps["name"] == comp, "id"].iloc[0])
            offs = query("""
                select o.id, coalesce(o.branch_name, '（拠点名なし）') as label
                from re_offices o where o.company_id = cast(:cid as uuid)
                order by o.branch_name
            """, {"cid": comp_id})
            off_opts = [NEW_ENTRY] + offs["label"].tolist()
            off = c[2].selectbox("拠点・支店", off_opts, index=min(1, len(off_opts) - 1),
                                 key=f"ao_{pid}_{comp_id}")
            if off != NEW_ENTRY:
                office_id = str(offs.loc[offs["label"] == off, "id"].iloc[0])

        # 担当者の候補は拠点が決まっているときだけ引ける。
        ppl = (query("""
                select id, name from re_persons
                where office_id = cast(:oid as uuid) and coalesce(is_current, true)
                  and name is not null
                order by name
            """, {"oid": office_id}) if office_id else None)

        # 記録できたら入力欄を空に戻す。**キーごと作り替える**のが確実で、
        # session_state から消す方法では form の中の値が残った（実測）。
        # 空にするのは記録できたときだけ。入力漏れで弾かれたときに消えると書き直しになる。
        seq = st.session_state.get(f"aseq_{pid}", 0)
        k = lambda name: f"a{name}_{pid}_{seq}"  # noqa: E731

        with st.form(f"addix_{pid}", border=False):
            f_comp = f_branch = None
            if new_company:
                cc = st.columns(2)
                f_comp = cc[0].text_input("会社名 *", placeholder="例：〇〇不動産",
                                          key=k("cname"))
                f_branch = cc[1].text_input("拠点・支店名", placeholder="例：名古屋支店",
                                            key=k("bname"))
            elif office_id is None:
                f_branch = st.text_input("拠点・支店名", placeholder="例：名古屋支店",
                                         key=k("bname"), help=f"{comp} に新しい拠点を作ります")

            c = st.columns([2, 2, 4])
            a_on = c[0].date_input("日付", value=datetime.date.today(), key=k("on"))
            a_loc = c[1].text_input("場所", key=k("loc"))
            if ppl is not None and not ppl.empty:
                # 拠点を切り替えると選択肢が総入れ替えになる。前の選択が残っていると
                # 「選択肢に無い値」で落ちるので、キーに拠点も混ぜる。
                a_who = c[2].multiselect("担当者", ppl["name"].tolist(),
                                         key=f"{k('who')}_{office_id}")
            else:
                a_who = []
                c[2].text_input("担当者", value="", disabled=True,
                                help="この拠点にはまだ担当者がいません。右の欄で登録できます")

            c = st.columns([4, 2])
            a_new_who = c[0].text_input("担当者を新しく登録", key=k("new"),
                                        placeholder="例：山田太郎、鈴木花子",
                                        help="「、」か「,」で区切ると複数登録します")
            # 融資可能額は銀行打診のときだけ。他の種別では書く場所が無い方が迷わない。
            a_amt = (c[1].number_input("融資可能額（万円）", value=None, step=100.0,
                                       format="%.0f", key=k("amt"))
                     if kind_db == "bank_inquiry" else None)

            a_content = st.text_area("内容 *", height=110, key=k("con"))
            ok = st.form_submit_button("記録する", type="primary")

        if not ok:
            return

        missing = ([] if a_content.strip() else ["内容"]) + \
                  (["会社名"] if new_company and not (f_comp or "").strip() else [])
        if missing:
            st.error("　".join(missing) + " を入力してください。")
            return

        if new_company:
            cname = f_comp.strip()
            # 同じ会社が別の種別で既に居ることがある（売買と賃貸を兼ねる先が7社ある）。
            # 表記ゆれを吸収する re_name_key で見て、居たら二重登録せず種別を足す。
            dup = query("select id from re_companies where name_key = re_name_key(:n)",
                        {"n": cname})
            if dup.empty:
                comp_id = str(uuid.uuid4())
                execute("""
                    insert into re_companies (id, name, name_key, kinds)
                    values (cast(:id as uuid), :name, re_name_key(:name), array[:k])
                """, {"id": comp_id, "name": cname, "k": ckind})
            else:
                comp_id = str(dup.iloc[0]["id"])
                execute("""
                    update re_companies set kinds = array_append(kinds, :k),
                           updated_at = now()
                     where id = cast(:id as uuid) and not (:k = any(kinds))
                """, {"id": comp_id, "k": ckind})
        if office_id is None:
            office_id = str(uuid.uuid4())
            execute("""
                insert into re_offices (id, company_id, branch_name)
                values (cast(:id as uuid), cast(:cid as uuid), :b)
            """, {"id": office_id, "cid": comp_id, "b": blank_to_none(f_branch)})

        iid = str(uuid.uuid4())
        execute("""
            insert into re_interactions (id, office_id, kind, occurred_on, location, content)
            values (cast(:id as uuid), cast(:oid as uuid), :k, :on, :loc, :content)
        """, {"id": iid, "oid": office_id, "k": kind_db, "on": a_on,
              "loc": blank_to_none(a_loc), "content": a_content.strip()})

        # 選んだ担当者 ＋ 新しく入れた担当者。新しい人は re_persons に作る。
        # person_name_raw に文字列で置くこともできるが、それだと取引先カルテの
        # 担当者一覧に出てこず、次に選ぶこともできない。
        person_ids = ([str(ppl.loc[ppl["name"] == n, "id"].iloc[0]) for n in a_who]
                      if a_who else [])
        for nm in re.split(r"[、,]", a_new_who or ""):
            nm = nm.strip()
            if not nm:
                continue
            # 同じ拠点に同名が既に居たら作らない。名前を打ち直して記録するたびに
            # 同じ人が増えていくのを防ぐ。
            same = (ppl.loc[ppl["name"] == nm, "id"] if ppl is not None
                    else pd.Series(dtype=object))
            if len(same):
                person_ids.append(str(same.iloc[0]))
                continue
            new_pid = str(uuid.uuid4())
            execute("""
                insert into re_persons (id, office_id, name)
                values (cast(:id as uuid), cast(:oid as uuid), :n)
            """, {"id": new_pid, "oid": office_id, "n": nm})
            person_ids.append(new_pid)
        person_ids = list(dict.fromkeys(person_ids))   # 選択と入力で重複したら1つに
        for person_id in person_ids:
            execute("""
                insert into re_interaction_persons (id, interaction_id, person_id)
                values (cast(:id as uuid), cast(:iid as uuid), cast(:pid as uuid))
            """, {"id": str(uuid.uuid4()), "iid": iid, "pid": person_id})

        # result（物件ごとの結果）は入れない。上の履歴は coalesce(result, content) で
        # content に落ちるので空でも表示される。移行データのように content の写しを
        # 作ると、あとで内容を直したときに写しの側だけ古いまま残る。
        execute("""
            insert into re_interaction_properties
              (id, interaction_id, property_id, property_name_raw, loanable_amount)
            values (cast(:id as uuid), cast(:iid as uuid), cast(:pid as uuid), :raw, :amt)
        """, {"id": str(uuid.uuid4()), "iid": iid, "pid": pid,
              "raw": blank_to_none(txt(prop["name"])), "amt": a_amt})

        # 基本形は「紹介元＝やりとり先」。紹介元がまだ空なら、いま記録した相手を
        # そのまま紹介元にする。ここを自動にしないと、同じ業者を2か所へ手で入れる
        # ことになり、実際に食い違いが起きていた。
        # 既に紹介元が入っているときは触らない（問い合わせ先が増えただけかもしれない）。
        if kind_db == "sales_contact" and not txt(prop["source_office_id"]):
            set_source(pid, office_id, person_ids[0] if person_ids else None)

        st.session_state[f"aseq_{pid}"] = seq + 1   # 入力欄を作り直して空に戻す
        st.rerun()


def render_interactions():
    """この物件に関わっている業者。売買仲介（紹介元こみ）→ 銀行・賃貸 → 追加 の順。"""
    st.markdown("#### 業者とのやりとり")
    # 担当者は1回の接触に複数人いることがあるので ' / ' で連ねる。
    # 名寄せできなかった相手は person_name_raw に原文が残っているのでそれを使う。
    hist = query("""
        select i.kind, o.id as office_id,
               i.occurred_on as 日付,
               c.name as 会社, o.branch_name as 拠点,
               coalesce(
                 (select string_agg(coalesce(p.name, ipe.person_name_raw), ' / ')
                    from re_interaction_persons ipe
                    left join re_persons p on p.id = ipe.person_id
                   where ipe.interaction_id = i.id),
                 -- 銀行打診は元Excelが「支店単位」の記録で、誰と話したかまで残っていない。
                 -- 接触に紐づく人がいないときは、その支店の現任担当者で代える。
                 (select string_agg(pe.name, ' / ')
                    from re_persons pe
                   where pe.office_id = i.office_id
                     and coalesce(pe.is_current, true))
               ) as 担当者,
               ip.loanable_amount as 融資可能額,
               coalesce(ip.result, i.content) as 内容
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id
        join re_offices o on o.id = i.office_id
        join re_companies c on c.id = o.company_id
        where ip.property_id = :pid
        order by i.occurred_on desc nulls last
    """, {"pid": str(prop["id"])})

    # 値のない欄をそのまま渡すと Streamlit が "None" という文字を描いてしまう（実際に出ていた）。
    # 日付は文字列にして、無い日付は空欄として見せる。
    if not hist.empty:
        hist["日付"] = (pd.to_datetime(hist["日付"], errors="coerce")
                        .dt.strftime("%Y-%m-%d").fillna(""))
        hist["融資可能額"] = pd.to_numeric(hist["融資可能額"], errors="coerce")
        for col in ["会社", "拠点", "担当者", "内容"]:
            hist[col] = hist[col].fillna("").astype(str)

    # 種別ごとに見たいものが違う。
    # - 銀行打診：どの銀行のどの支店の誰が何と言ったか。会社名（銀行名）まで要る
    # - 賃貸ヒアリング：拠点名に会社名が入っている（「ニッシー可児支店」等）ので会社は省く
    # 「内容」は表に入れない。st.dataframe は1セル1行しか描けず、賃貸ヒアリングは
    # 700〜1100字あって畳まれてしまうため、表の下に全文で出す（_content_blocks）。
    # 3つめは「相手先の種別」。行を選んだときに、どの取引先画面へ飛ぶかを決める。
    groups = [("bank_inquiry", "銀行打診", ["会社", "拠点", "担当者", "融資可能額"], "bank"),
              ("rental_hearing", "賃貸ヒアリング", ["日付", "拠点", "担当者"], "rental_agency")]

    WIDTH = {"日付": 100, "会社": 240, "拠点": 260, "担当者": 220, "融資可能額": 120}

    render_sales_brokers()

    shown_any = False
    for kind, label, cols, ckind in groups:
        src = hist[hist["kind"] == kind]
        if src.empty:
            continue
        # 1件も値がない列は出さない。融資可能額は現状139件すべて空で、
        # 空の列があるだけで表が読みにくくなる。値が入れば自動でまた出る。
        # notna は数値列（NaN）用、空文字判定は文字列列用。両方見ないと取りこぼす
        vis = [c for c in cols
               if src[c].notna().any()
               and (src[c].astype(str).str.strip() != "").any()]
        table = src[vis + ["office_id"]]
        shown_any = True
        st.caption(f"{label}　{len(table)} 件　—　行を選ぶと相手先の担当者を直せます")
        conf = {c: (money(c) if c == "融資可能額"
                    else st.column_config.TextColumn(c, width=WIDTH[c])) for c in vis}
        conf["office_id"] = None  # 飛び先を持たせるだけの列。画面には出さない
        ev = st.dataframe(table, width="stretch", hide_index=True, column_config=conf,
                          on_select="rerun", selection_mode="single-row",
                          key=f"hist_{kind}_{prop['id']}")
        rows = ev.selection.rows
        if rows:
            # 戻り先を渡しておくと、直したあと「← 物件詳細に戻る」で帰ってこられる
            goto_office_edit(table.iloc[rows[0]]["office_id"], ckind, str(prop["id"]))
        _content_blocks(src)

    if not shown_any:
        st.caption("銀行打診・賃貸ヒアリングの記録はまだありません。")

    render_add_interaction()

    # 元Excelの原文（構造化前）。参照用に畳んでおく。
    if txt(prop["bank_inquiry_result"]):
        with st.expander("銀行打診結果の原文（元Excel）"):
            st.write(prop["bank_inquiry_result"])


def render_edit_form():
    """全入力項目を1つのフォームにまとめ、1回のUPDATEで保存する。

    並びは元Excel「■RC一般 V2」の列順に合わせる：
      B返信日付 C所在地 D物件名 → X返信手段 Y問合せ媒体 AA入力メモ
      → AB EV AC浄化槽 AD無料NET/CATV AEハザード AF入居数 AG戸数 AH駐車場 AI敷地外駐車場
      → AL建築日 AM販売価格 AR指値後価格 AU路価実 AW固税実
      → AX土地面積 AY用途地域 AZ用途地域係数 BA土地形状係数 BC延床面積
      → BD満室年収 BE現況年収 BH EV費等の追加
    """
    st.markdown("#### 物件情報")
    structures = query("select structure from re_structure_types order by sort_order"
                       )["structure"].tolist()

    with st.form(key=f"edit_{prop['id']}"):
        # B / C / D ＋ X / Y
        c = st.columns([2, 3, 3, 2, 2])
        # 元Excelの列名は「返信日付」だが、実態はこのDBに登録した日付なので画面上は「登録日付」
        f_reply = c[0].date_input("登録日付", day(prop["reply_date"]))
        f_addr = c[1].text_input("所在地", txt(prop["address"]))
        f_name = c[2].text_input("物件名", txt(prop["name"]))
        f_contact = c[3].text_input("返信手段", txt(prop["contact_method"]))
        f_channel = c[4].text_input("問合せ媒体", txt(prop["inquiry_channel"]))

        # AA 入力メモ ＋ 版のラベル
        c = st.columns([3, 2])
        f_input_memo = c[0].text_input("入力メモ", txt(prop["input_memo"]),
                                       help="満室年収の計算根拠など、数値の出どころのメモ")
        f_label = c[1].text_input("版のラベル", txt(prop["scenario_label"]),
                                  help="例：サブリース解除後／2025年版／現地確認反映")

        # AB〜AI 設備・状況
        c = st.columns(8)

        def pick(col, label, cur, help=None):
            v = txt(cur)
            return col.selectbox(label, ARINASHI,
                                 index=ARINASHI.index(v) if v in ARINASHI else 0, help=help)

        f_ev = pick(c[0], "EV", prop["has_elevator"])
        f_septic = pick(c[1], "浄化槽", prop["has_septic_tank"])
        f_net = pick(c[2], "無料NET/CATV", prop["free_internet"],
                     help="元Excel AD列。ケーブルテレビ・インターネットの無料提供")
        f_hazard = c[3].text_input("ハザード", txt(prop["hazard"]),
                                   help="例：浸水／土砂災害／津波5m")
        f_occ = c[4].number_input("入居数", value=num(prop["occupied_units"]),
                                  step=1.0, format="%.0f")
        f_total = c[5].number_input("戸数", value=num(prop["total_units"]),
                                    step=1.0, format="%.0f")
        f_park = c[6].number_input("駐車場", value=num(prop["parking_spaces"]),
                                   step=1.0, format="%.0f")
        f_expark = c[7].text_input("敷地外駐車場", txt(prop["external_parking"]))

        # AL / AM / AR / AU / AW ＋ 構造
        # 金額・面積・年収は整数で表示する（format は見た目だけを変え、値は保持される）。
        # 路価実は小数第1位まで。係数は %（100倍）で入力してもらい、保存時に戻す。
        c = st.columns(6)
        cur_st = txt(prop["structure"])
        f_struct = c[0].selectbox("構造", structures,
                                  index=structures.index(cur_st) if cur_st in structures else 0)
        f_built = c[1].date_input("建築日", day(prop["built_date"]),
                                  min_value=datetime.date(1950, 1, 1))
        f_price = c[2].number_input("販売価格(万円)", value=num(prop["purchase_price"]),
                                    step=10.0, format="%.0f")
        f_nego = c[3].number_input("指値後物件価格(万円)", value=num(prop["negotiated_price"]),
                                   step=10.0, format="%.0f")
        f_road = c[4].number_input("路価実", value=num(prop["road_price_actual"]),
                                   step=0.1, format="%.1f", help="空欄なら 1.0 として計算")
        f_tax = c[5].number_input("固税実(万円)", value=num(prop["property_tax"]),
                                  step=1.0, format="%.0f",
                                  help="空欄なら建物評価から自動で仮計算")

        # AX / AY / AZ / BA / BC
        c = st.columns(5)
        f_land = c[0].number_input("土地面積(㎡)", value=num(prop["land_area"]),
                                   step=1.0, format="%.0f")
        f_zoning = c[1].text_input("用途地域", txt(prop["zoning"]))
        zc, sc = num(prop["zone_coef"]), num(prop["shape_coef"])
        f_zcoef_pct = c[2].number_input("用途地域係数(%)",
                                        value=None if zc is None else round(zc * 100),
                                        step=5, format="%d",
                                        help="商業110 近商105 住居100 準工80 工業70")
        f_scoef_pct = c[3].number_input("土地形状係数(%)",
                                        value=None if sc is None else round(sc * 100),
                                        step=5, format="%d")
        f_floor = c[4].number_input("延床面積(㎡)", value=num(prop["floor_area"]),
                                    step=1.0, format="%.0f")

        # BD / BE / BH ＋ 計算の前提
        c = st.columns(5)
        f_full = c[0].number_input("満室年収(万円)", value=num(prop["full_income"]),
                                   step=1.0, format="%.0f")
        f_curr = c[1].number_input("現況年収(万円)", value=num(prop["current_income"]),
                                   step=1.0, format="%.0f")
        f_extra = c[2].number_input("EV費等の追加(万円)", value=num(prop["extra_cost"]),
                                    step=1.0, format="%.0f",
                                    help="CATV・インターネット・浄化槽の維持費などの年額")
        f_rate = c[3].number_input("銀行提示金利", value=num(prop["bank_offered_rate"]),
                                   step=0.001, format="%.3f",
                                   help="例：0.02（2.0%）。空欄なら標準の1.5%で計算")
        f_life = c[4].number_input("耐用年数の上書き(年)", value=num(prop["legal_useful_life"]),
                                   step=1.0, format="%.0f",
                                   help=f"通常は空欄。空欄なら構造から自動で {prop['useful_life']:.0f} 年")

        saved = st.form_submit_button("物件情報を保存", type="primary")

    if saved:
        execute(UPDATE_SQL, {
            "id": str(prop["id"]),
            "reply_date": f_reply, "address": blank_to_none(f_addr),
            "name": blank_to_none(f_name),
            "contact_method": blank_to_none(f_contact),
            "inquiry_channel": blank_to_none(f_channel),
            "has_elevator": blank_to_none(f_ev), "has_septic_tank": blank_to_none(f_septic),
            "free_internet": blank_to_none(f_net), "hazard": blank_to_none(f_hazard),
            "occupied_units": f_occ, "total_units": f_total,
            "parking_spaces": f_park, "external_parking": blank_to_none(f_expark),
            "structure": f_struct, "built_date": f_built,
            "purchase_price": f_price, "negotiated_price": f_nego,
            "road_price_actual": f_road, "property_tax": f_tax,
            "land_area": f_land, "zoning": blank_to_none(f_zoning),
            # 係数は画面では%で入力してもらうので、保存時に100で割って元に戻す
            "zone_coef": None if f_zcoef_pct is None else f_zcoef_pct / 100,
            "shape_coef": None if f_scoef_pct is None else f_scoef_pct / 100,
            "floor_area": f_floor,
            "full_income": f_full, "current_income": f_curr, "extra_cost": f_extra,
            "bank_offered_rate": f_rate, "legal_useful_life": f_life,
            "scenario_label": blank_to_none(f_label),
            "input_memo": blank_to_none(f_input_memo),
        })
        st.success("保存しました。")
        st.rerun()

    if txt(prop["name_raw"]) and prop["name_raw"] != prop["name"]:
        st.caption(f"元Excelの物件名：{prop['name_raw']}")


def render_versions():
    st.caption("同じ物件の前提違い・時点違いを横に並べています。")
    cmp = query("""
        select "版", "元excel行", "販売価格", "指値後価格", "満室利回",
               "積算比率", "実質cf", "cf基準", "登録日付"
        from re_properties_v
        where "物件グループ" = :gid
        order by "元excel行"
    """, {"gid": str(prop["group_id"])})
    cmp_styled = cmp.style.set_properties(
        subset=["満室利回", "積算比率", "実質cf", "cf基準"],
        **{"background-color": CALC_BG})
    st.dataframe(cmp_styled, width="stretch", hide_index=True,
                column_config={
                    "販売価格": money("販売価格"),
                    "指値後価格": money("指値後価格"),
                    "満室利回": ratio("満室利回り"),
                    "積算比率": ratio("積算比率"),
                    "実質cf": money("実質CF"),
                    "cf基準": st.column_config.TextColumn("CF基準"),
                })


# ── 描画 ────────────────────────────────────────────────────
def render_main():
    ar, row = render_summary()
    render_memo()
    render_interactions()
    render_edit_form()
    # 計算値の内訳は、ふだんは見ない。物件情報のさらに下に畳んで置く
    render_calc_detail(ar, row)


# 版が1つだけならタブを出さず、まるごと1画面にする。
if len(versions) > 1:
    main_tab, ver_tab = st.tabs(["この版", "版の比較"])
    with main_tab:
        render_main()
    with ver_tab:
        render_versions()
else:
    render_main()
