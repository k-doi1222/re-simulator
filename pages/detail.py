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
    re_target_price(id, 150) as target150,
    re_target_price(id, 200) as target200
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
    """この物件の要点。ぱっと目に入るよう、上部に太字でまとめて置く。"""
    purchase_price = num(prop["purchase_price"])

    # ── 指値後価格（入力値。ここだけ直接変えられる）────────
    top = st.columns([2, 1, 5])
    with top[0]:
        ar = st.number_input(
            "指値後価格（万円）",
            value=float(num(prop["negotiated_price"]) or purchase_price or 0),
            step=10.0, key=f"ar_{prop['id']}")
    with top[1]:
        st.write("")  # ラベル分の高さを合わせる
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

    def b(label, value):
        """ラベルと値を、本文と同じ文字サイズの太字で出す。"""
        st.markdown(f"<div style='font-size:0.78rem;color:#666'>{label}</div>"
                    f"<div style='font-weight:700'>{value}</div>", unsafe_allow_html=True)

    # ── 1段目：返信日付・物件名・所在地・CF基準 ───────────
    c = st.columns([1.2, 2.4, 2.8, 1.2])
    with c[0]:
        b("返信日付", str(prop["reply_date"]) if prop["reply_date"] else "—")
    with c[1]:
        b("物件名", prop["name"] or "（物件名なし）")
    with c[2]:
        b("所在地", txt(prop["address"]) or "—")
    with c[3]:
        b("CF基準", row["c_bu"] or "—")

    # ── 2段目：目標到達価格・築年数・価格・積算比率 ────────
    t150, t200 = num(prop["target150"]), num(prop["target200"])

    def target_text(price):
        if price is None or price <= 0:
            return "到達不可"
        if price >= purchase_price:
            return f"{purchase_price:,.0f} 万円（現価格で到達）"
        off = purchase_price - price
        return f"{price:,.0f} 万円（▲{off:,.0f} 万円・{off / purchase_price * 100:.1f}%）"

    c = st.columns([2.2, 2.2, 1.0, 1.4, 1.2])
    with c[0]:
        b("△150にする指値後価格", target_text(t150))
    with c[1]:
        b("○200にする指値後価格", target_text(t200))
    with c[2]:
        b("築年数", f"{row['c_bb']:.0f} 年" if pd.notna(row["c_bb"]) else "—")
    with c[3]:
        b("価格（指値後）", f"{ar:,.0f} 万円")
    with c[4]:
        b("積算比率", f"{row['c_bp'] * 100:.0f}%" if pd.notna(row["c_bp"]) else "—")

    st.caption(f"販売価格 {purchase_price:,.0f} 万円　／　"
              f"現在の値引き率 {(1 - ar / purchase_price) * 100:.1f}%　／　"
              f"到達価格は販売価格を基準に算出　／　"
              f"構造 {txt(prop['structure']) or '未設定'}・法定耐用年数 {prop['useful_life']:.0f}年")
    return ar, row


def render_calc_detail(ar, row):
    """判定まわりの計算値。要点に入りきらないものはここに畳んでおく。"""
    if row is None:
        return
    with st.container(key="calc_block"):
        m = st.columns(4)
        m[0].metric("実質CF", f"{row['c_bt']:,.0f} 万円" if pd.notna(row["c_bt"]) else "—")
        m[1].metric("CF基準", row["c_bu"] or "—")
        m[2].metric("積算評価", f"{row['c_bo']:,.0f} 万円" if pd.notna(row["c_bo"]) else "—")
        m[3].metric("満室利回", f"{row['c_bq'] * 100:.2f}%" if pd.notna(row["c_bq"]) else "—")

    with st.expander("その他の計算値"):
        with st.container(key="detail_calc"):
            g = st.columns(6)
            g[0].metric("築年数", f"{row['c_bb']:.0f}年" if pd.notna(row["c_bb"]) else "—")
            g[1].metric("融資年数", f"{row['c_by']:.0f}年" if pd.notna(row["c_by"]) else "—")
            g[2].metric("土地評価", f"{row['c_bm']:,.0f}" if pd.notna(row["c_bm"]) else "—")
            g[3].metric("建物評価", f"{row['c_bn']:,.0f}" if pd.notna(row["c_bn"]) else "—")
            g[4].metric("積算評価", f"{row['c_bo']:,.0f}" if pd.notna(row["c_bo"]) else "—")
            g[5].metric("年間返済", f"{row['c_cd']:,.0f}" if pd.notna(row["c_cd"]) else "—")
            g2 = st.columns(6)
            g2[0].metric("現況利回", f"{row['c_br'] * 100:.2f}%" if pd.notna(row["c_br"]) else "—")
            g2[1].metric("購入諸経費", f"{row['c_bw']:,.0f}" if pd.notna(row["c_bw"]) else "—")
            g2[2].metric("満室時CF", f"{row['c_bs']:,.0f}" if pd.notna(row["c_bs"]) else "—")
            g2[3].metric("現況CF", f"{row['c_bv']:,.0f}" if pd.notna(row["c_bv"]) else "—")
            g2[4].metric("固都税(仮)", f"{row['c_av']:,.0f}" if pd.notna(row["c_av"]) else "—")
            g2[5].metric("7年通算", f"{row['c_dq']:,.0f}" if pd.notna(row["c_dq"]) else "—")


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
    hist = query("""
        select i.kind,
               c.name as 会社, o.branch_name as 拠点,
               i.occurred_on as 日付,
               ip.loanable_amount as 融資可能額,
               coalesce(ip.result, i.content) as 内容
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id
        join re_offices o on o.id = i.office_id
        join re_companies c on c.id = o.company_id
        where ip.property_id = :pid
        order by i.occurred_on desc nulls last
    """, {"pid": str(prop["id"])})

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
        cols = ["会社", "拠点", "日付"] + (["融資可能額"] if with_amount else []) + ["内容"]
        st.dataframe(part[cols], width="stretch", hide_index=True,
                    column_config={"融資可能額": money("融資可能額"), "内容": longtext("内容")})

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
        f_reply = c[0].date_input("返信日付", day(prop["reply_date"]))
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
        f_occ = c[4].number_input("入居数", value=num(prop["occupied_units"]), step=1.0)
        f_total = c[5].number_input("戸数", value=num(prop["total_units"]), step=1.0)
        f_park = c[6].number_input("駐車場", value=num(prop["parking_spaces"]), step=1.0)
        f_expark = c[7].text_input("敷地外駐車場", txt(prop["external_parking"]))

        # AL / AM / AR / AU / AW ＋ 構造
        c = st.columns(6)
        cur_st = txt(prop["structure"])
        f_struct = c[0].selectbox("構造", structures,
                                  index=structures.index(cur_st) if cur_st in structures else 0)
        f_built = c[1].date_input("建築日", day(prop["built_date"]),
                                  min_value=datetime.date(1950, 1, 1))
        f_price = c[2].number_input("販売価格(万円)", value=num(prop["purchase_price"]), step=10.0)
        f_nego = c[3].number_input("指値後物件価格(万円)", value=num(prop["negotiated_price"]),
                                   step=10.0)
        f_road = c[4].number_input("路価実", value=num(prop["road_price_actual"]), step=0.1,
                                   help="空欄なら 1.0 として計算")
        f_tax = c[5].number_input("固税実(万円)", value=num(prop["property_tax"]), step=1.0,
                                  help="空欄なら建物評価から自動で仮計算")

        # AX / AY / AZ / BA / BC
        c = st.columns(5)
        f_land = c[0].number_input("土地面積(㎡)", value=num(prop["land_area"]), step=1.0)
        f_zoning = c[1].text_input("用途地域", txt(prop["zoning"]))
        f_zcoef = c[2].number_input("用途地域係数", value=num(prop["zone_coef"]), step=0.05,
                                    help="商業110 近商105 住居100 準工80 工業70")
        f_scoef = c[3].number_input("土地形状係数", value=num(prop["shape_coef"]), step=0.05)
        f_floor = c[4].number_input("延床面積(㎡)", value=num(prop["floor_area"]), step=1.0)

        # BD / BE / BH ＋ 計算の前提
        c = st.columns(5)
        f_full = c[0].number_input("満室年収(万円)", value=num(prop["full_income"]), step=1.0)
        f_curr = c[1].number_input("現況年収(万円)", value=num(prop["current_income"]), step=1.0)
        f_extra = c[2].number_input("EV費等の追加(万円)", value=num(prop["extra_cost"]), step=1.0,
                                    help="CATV・インターネット・浄化槽の維持費などの年額")
        f_rate = c[3].number_input("銀行提示金利", value=num(prop["bank_offered_rate"]),
                                   step=0.001, format="%.3f",
                                   help="例：0.02（2.0%）。空欄なら標準の1.5%で計算")
        f_life = c[4].number_input("耐用年数の上書き(年)", value=num(prop["legal_useful_life"]),
                                   step=1.0,
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
            "zone_coef": f_zcoef, "shape_coef": f_scoef, "floor_area": f_floor,
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
               "積算比率", "実質cf", "cf判定", "返信日付"
        from re_properties_v
        where "物件グループ" = :gid
        order by "元excel行"
    """, {"gid": str(prop["group_id"])})
    cmp_styled = cmp.style.set_properties(
        subset=["満室利回", "積算比率", "実質cf", "cf判定"],
        **{"background-color": CALC_BG})
    st.dataframe(cmp_styled, width="stretch", hide_index=True,
                column_config={
                    "販売価格": money("販売価格"),
                    "指値後価格": money("指値後価格"),
                    "満室利回": ratio("満室利回"),
                    "積算比率": ratio("積算比率"),
                    "実質cf": money("実質CF"),
                    "cf判定": st.column_config.TextColumn("判定"),
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
