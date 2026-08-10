"""物件詳細 — UC-2 指値の逆算、UC-3 版の比較、物件情報の編集

1画面で眺めて編集できるよう縦に詰めて並べる。並びは
  シミュレーション → メモ・コメント → 話したこと → 物件情報（元Excelの列順）
版の比較だけは物件が複数版を持つときにタブで分ける。

「実質CF ÷ 指値後価格」は指値後価格に対して単調に動く（価格を下げるほど良くなる）。
PMT・税金などの計算式そのものはすべてSQL側の re_calc_property_analysis に置いたまま、
2点だけ実際に評価してもらい、そこから「判定値150/200に乗る価格」を代数で解く。
"""
import datetime

import pandas as pd
import streamlit as st

from auth import require_password
from db import execute, query
from theme import CALC_BG, compact_css, count, longtext, money, ratio

require_password()  # サイドバー経由の直接遷移で認証をすり抜けないよう、各ページ自身でも確認する
compact_css()

RAW_COLS = """
    id, excel_row, name, name_raw, address, structure, reply_date, zoning, status,
    memo, input_memo, broker_comment, bank_inquiry_result,
    contact_method, inquiry_channel,
    purchase_price, negotiated_price, land_area, road_price_actual, zone_coef, shape_coef,
    floor_area, built_date, full_income, current_income, extra_cost, property_tax,
    occupied_units, total_units, parking_spaces, external_parking,
    has_elevator, has_septic_tank, free_internet, hazard,
    legal_useful_life, bank_offered_rate, scenario_label,
    coalesce(legal_useful_life, re_useful_life_by_structure(structure)) as useful_life,
    -- 目標判定に乗せる価格は100万円刻み。丸めた分だけ目標を上回るので、
    -- 減額幅ではなく「その価格でのCF基準」を見せる。
    (select price         from re_target_price_detail(id, 150)) as t150_price,
    (select discount_rate from re_target_price_detail(id, 150)) as t150_rate,
    (select cf_mark || cf_value::text
       from re_target_price_detail(id, 150))                    as t150_cf,
    (select price         from re_target_price_detail(id, 200)) as t200_price,
    (select discount_rate from re_target_price_detail(id, 200)) as t200_rate,
    (select cf_mark || cf_value::text
       from re_target_price_detail(id, 200))                    as t200_cf
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
    select {RAW_COLS}, coalesce(parent_property_id, id) as group_id
    from re_properties
    where coalesce(parent_property_id, id) = (
        select coalesce(parent_property_id, id) from re_properties where id = :id
    )
    order by excel_row
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

    st.caption(f"構造 {txt(prop['structure']) or '未設定'}"
              f"・法定耐用年数 {prop['useful_life']:.0f}年"
              f"　／　元Excel {prop['excel_row']}行目")
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

    with st.expander("計算値の内訳", expanded=True):
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


def render_interactions():
    """この物件について誰と何を話したかを、種別ごとに分けて出す。"""
    st.markdown("#### この物件について話したこと")
    # 会社名は拠点名（「ニッシー可児支店」等）に含まれるので、この画面では出さない。
    # 担当者は1回の接触に複数人いることがあるので ' / ' で連ねる。
    # 名寄せできなかった相手は person_name_raw に原文が残っているのでそれを使う。
    hist = query("""
        select i.kind,
               i.occurred_on as 日付,
               o.branch_name as 拠点,
               (select string_agg(coalesce(p.name, ipe.person_name_raw), ' / ')
                  from re_interaction_persons ipe
                  left join re_persons p on p.id = ipe.person_id
                 where ipe.interaction_id = i.id) as 担当者,
               ip.loanable_amount as 融資可能額,
               coalesce(ip.result, i.content) as 内容
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id
        join re_offices o on o.id = i.office_id
        where ip.property_id = :pid
        order by i.occurred_on desc nulls last
    """, {"pid": str(prop["id"])})

    # 値のない欄をそのまま渡すと Streamlit が "None" という文字を描いてしまう（実際に出ていた）。
    # 日付は文字列にして、無い日付は空欄として見せる。
    if not hist.empty:
        hist["日付"] = (pd.to_datetime(hist["日付"], errors="coerce")
                        .dt.strftime("%Y-%m-%d").fillna(""))
        hist["融資可能額"] = pd.to_numeric(hist["融資可能額"], errors="coerce")
        for col in ["拠点", "担当者", "内容"]:
            hist[col] = hist[col].fillna("").astype(str)

    groups = [("bank_inquiry", "銀行打診", True),
              ("rental_hearing", "賃貸ヒアリング", False),
              ("sales_contact", "売買仲介とのやりとり", False)]
    shown_any = False
    for kind, label, with_amount in groups:
        part = hist[hist["kind"] == kind]
        if part.empty:
            continue
        shown_any = True
        st.caption(f"{label}　{len(part)} 件")
        cols = ["日付", "拠点", "担当者"] + (["融資可能額"] if with_amount else []) + ["内容"]
        # 1件も値がない列は出さない。融資可能額は現状139件すべて空で、
        # 空の列があるだけで表が読みにくくなる。値が入れば自動でまた出る。
        cols = [c for c in cols if part[c].notna().any()
                and (part[c].astype(str).str.strip() != "").any()]
        st.dataframe(part[cols], width="stretch", hide_index=True,
                    column_config={
                        "日付": st.column_config.TextColumn("日付", width="small"),
                        "拠点": st.column_config.TextColumn("拠点", width="medium"),
                        "担当者": st.column_config.TextColumn("担当者", width="small"),
                        "融資可能額": money("融資可能額"),
                        "内容": longtext("内容"),
                    })

    if not shown_any:
        st.caption("この物件についての打診・ヒアリングの記録はまだありません。")

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
    st.caption("並びは元Excel「■RC一般 V2」の列順に合わせています。")
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
    render_calc_detail(ar, row)
    render_edit_form()


# 版が1つだけならタブを出さず、まるごと1画面にする。
if len(versions) > 1:
    main_tab, ver_tab = st.tabs(["この版", "版の比較"])
    with main_tab:
        render_main()
    with ver_tab:
        render_versions()
else:
    render_main()
