"""物件詳細 — UC-2 指値の逆算、UC-3 版の比較、物件情報の編集

1画面で眺めて編集できるよう、シミュレーションと全入力項目を縦に詰めて並べる。
版の比較だけは物件が複数版を持つときにタブで分ける。

「実質CF ÷ 指値後価格」は指値後価格に対して単調に動く（価格を下げるほど良くなる）。
PMT・税金などの計算式そのものはすべてSQL側の re_calc_property_analysis に置いたまま、
2点だけ実際に評価してもらい、そこから「判定値150/200/250に乗る価格」を代数で解く。
"""
import datetime
import math

import pandas as pd
import streamlit as st

from auth import require_password
from db import execute, query
from theme import CALC_BG, compact_css

require_password()  # サイドバー経由の直接遷移で認証をすり抜けないよう、各ページ自身でも確認する
compact_css()

RAW_COLS = """
    id, excel_row, name, name_raw, address, structure, reply_date, zoning,
    memo, input_memo, broker_comment, bank_inquiry_result,
    contact_method, inquiry_channel,
    purchase_price, negotiated_price, land_area, road_price_actual, zone_coef, shape_coef,
    floor_area, built_date, full_income, current_income, extra_cost, property_tax,
    occupied_units, total_units, parking_spaces, external_parking,
    has_elevator, has_septic_tank, free_internet, hazard,
    legal_useful_life, bank_offered_rate, scenario_label,
    coalesce(legal_useful_life, re_useful_life_by_structure(structure)) as useful_life
"""

# 計算関数に渡す入力値。どちらのSQLでも同じ列名で使う。
INPUT_COLS = ["purchase_price", "land_area", "road_price_actual", "zone_coef", "shape_coef",
             "floor_area", "built_date", "full_income", "current_income", "extra_cost",
             "property_tax", "useful_life"]

# 1回だけ評価する版（今のスライダー値での判定）。
LIVE_SQL = """
    select * from re_calc_property_analysis(
      :purchase_price, :ar, :land_area, :road_price_actual, :zone_coef, :shape_coef,
      :floor_area, :built_date, :full_income, :current_income, :extra_cost, :property_tax,
      :useful_life, 0.015, 0.09, 0.2, 0.04, 19, current_date, 1, null
    )
"""

# 2点だけ評価してソルバーの係数を出す版。他の入力は固定してARだけ2通り試す。
# 注意：SQLAlchemyの text() は「:param::型」(バインド直後にキャスト)を誤解釈してSQL構文エラーになる。
# cast(:param as 型) の書き方で回避する。
SAMPLES_SQL = """
    with p as (
      select cast(:purchase_price as float8) as purchase_price,
             cast(:land_area as float8) as land_area,
             cast(:road_price_actual as float8) as road_price_actual,
             cast(:zone_coef as float8) as zone_coef,
             cast(:shape_coef as float8) as shape_coef,
             cast(:floor_area as float8) as floor_area,
             cast(:built_date as date) as built_date,
             cast(:full_income as float8) as full_income,
             cast(:current_income as float8) as current_income,
             cast(:extra_cost as float8) as extra_cost,
             cast(:property_tax as float8) as property_tax,
             cast(:useful_life as float8) as useful_life
    ),
    t(ar_test) as (values (cast(:ar1 as float8)), (cast(:ar2 as float8)))
    select t.ar_test, c.c_bt
    from p, t
    cross join lateral re_calc_property_analysis(
      p.purchase_price, t.ar_test, p.land_area, p.road_price_actual, p.zone_coef, p.shape_coef,
      p.floor_area, p.built_date, p.full_income, p.current_income, p.extra_cost, p.property_tax,
      p.useful_life, 0.015, 0.09, 0.2, 0.04, 19, current_date, 1, null
    ) c
"""

UPDATE_SQL = """
    update re_properties set
      name = :name, address = :address, reply_date = :reply_date,
      structure = :structure, built_date = :built_date, zoning = :zoning,
      purchase_price = :purchase_price, negotiated_price = :negotiated_price,
      land_area = :land_area, floor_area = :floor_area,
      zone_coef = :zone_coef, shape_coef = :shape_coef,
      road_price_actual = :road_price_actual,
      full_income = :full_income, current_income = :current_income,
      occupied_units = :occupied_units, total_units = :total_units,
      property_tax = :property_tax, extra_cost = :extra_cost,
      parking_spaces = :parking_spaces, external_parking = :external_parking,
      has_elevator = :has_elevator, has_septic_tank = :has_septic_tank,
      free_internet = :free_internet, hazard = :hazard,
      bank_offered_rate = :bank_offered_rate, legal_useful_life = :legal_useful_life,
      scenario_label = :scenario_label,
      memo = :memo, input_memo = :input_memo, broker_comment = :broker_comment,
      bank_inquiry_result = :bank_inquiry_result,
      contact_method = :contact_method, inquiry_channel = :inquiry_channel,
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
head = st.columns([1, 4]) if len(versions) > 1 else st.columns([1, 4])
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

st.markdown(f"### {prop['name'] or '（物件名なし）'}")
st.caption(f"{txt(prop['address']) or '所在地不明'}　／　元Excel {prop['excel_row']}行目"
           f"　／　構造 {txt(prop['structure']) or '未設定'}"
           f"　／　法定耐用年数 {prop['useful_life']:.0f}年（構造から自動）")


def render_simulation():
    """指値スライダー・判定・ソルバーを描く。値の保存はここでは行わない。"""
    if num(prop["purchase_price"]) in (None, 0):
        st.warning("販売価格が未入力のため、シミュレーションできません。下のフォームで入力してください。")
        return

    purchase_price = num(prop["purchase_price"])
    default_ar = num(prop["negotiated_price"]) or purchase_price
    lo = max(0.0, round(purchase_price * 0.3, -1))
    hi = round(purchase_price * 1.05, -1)

    sl, btn = st.columns([5, 1], vertical_alignment="bottom")
    with sl:
        ar = st.slider(f"指値後価格（万円）　販売価格 {purchase_price:,.0f} 万円",
                       min_value=lo, max_value=hi,
                       value=min(max(default_ar, lo), hi), step=10.0,
                       key=f"ar_slider_{prop['id']}")
    with btn:
        if st.button("価格を保存", type="primary", width="stretch"):
            execute("update re_properties set negotiated_price = :ar, updated_at = now() "
                    "where id = :id", {"ar": ar, "id": str(prop["id"])})
            st.success("保存しました")
            st.rerun()

    calc_in = {c: num(prop[c]) if c != "built_date" else day(prop[c]) for c in INPUT_COLS}
    row = query(LIVE_SQL, {**calc_in, "ar": ar}).iloc[0]

    left, right = st.columns([1, 1])
    with left:
        st.caption(f"この価格での判定（値引き率 {(1 - ar / purchase_price) * 100:.1f}%）")
        with st.container(key="calc_block"):
            m = st.columns(4)
            m[0].metric("実質CF", f"{row['c_bt']:.1f}" if pd.notna(row["c_bt"]) else "—")
            m[1].metric("判定", row["c_bu"] or "—")
            m[2].metric("積算比率", f"{row['c_bp']:.2f}" if pd.notna(row["c_bp"]) else "—")
            m[3].metric("満室利回", f"{row['c_bq'] * 100:.2f}%" if pd.notna(row["c_bq"]) else "—")

    with right:
        st.caption("目標の判定に乗せる指値後価格")
        samples = query(SAMPLES_SQL, {**calc_in, "ar1": purchase_price,
                                      "ar2": purchase_price * 0.5})
        ar1, bt1 = samples.iloc[0]["ar_test"], samples.iloc[0]["c_bt"]
        ar2, bt2 = samples.iloc[1]["ar_test"], samples.iloc[1]["c_bt"]
        with st.container(key="solver_block"):
            cols = st.columns(3)
            if pd.notna(bt1) and pd.notna(bt2) and ar1 != ar2:
                k = (bt1 - bt2) / (ar2 - ar1)
                C = bt1 + k * ar1
                for col, mark, target in zip(cols, ["△", "○", "◎"], [150, 200, 250]):
                    denom = target + 10000 * k
                    raw = 10000 * C / denom if denom else math.nan
                    # 判定値の表示はSQL側でfloor()するため、境界ちょうどだと丸め誤差で
                    # 1つ下に落ちることがある。10万円単位で切り下げて安全側に倒す。
                    price = math.floor(raw / 10) * 10 if math.isfinite(raw) else math.nan
                    with col:
                        if not math.isfinite(price) or price <= 0:
                            st.metric(f"{mark}{target}", "不可")
                        elif price >= purchase_price:
                            st.metric(f"{mark}{target}", "現価格で到達")
                        else:
                            st.metric(f"{mark}{target}", f"{price:,.0f}",
                                      f"{(1 - price / purchase_price) * 100:.1f}% 指値")
            else:
                st.caption("満室年収などの入力が不足しており、逆算できません。")

    with st.expander("その他の計算値"):
        with st.container(key="detail_calc"):
            g = st.columns(6)
            g[0].metric("築年数", f"{row['c_bb']:.0f}年" if pd.notna(row["c_bb"]) else "—")
            g[1].metric("融資年数", f"{row['c_by']:.0f}年" if pd.notna(row["c_by"]) else "—")
            g[2].metric("土地評価", f"{row['c_bm']:,.0f}" if pd.notna(row["c_bm"]) else "—")
            g[3].metric("建物評価", f"{row['c_bn']:,.0f}" if pd.notna(row["c_bn"]) else "—")
            g[4].metric("積算評価", f"{row['c_bo']:,.0f}" if pd.notna(row["c_bo"]) else "—")
            g[5].metric("年間返済", f"{row['c_cd']:,.1f}" if pd.notna(row["c_cd"]) else "—")
            g2 = st.columns(6)
            g2[0].metric("現況利回", f"{row['c_br'] * 100:.2f}%" if pd.notna(row["c_br"]) else "—")
            g2[1].metric("購入諸経費", f"{row['c_bw']:,.1f}" if pd.notna(row["c_bw"]) else "—")
            g2[2].metric("満室時CF", f"{row['c_bs']:.1f}" if pd.notna(row["c_bs"]) else "—")
            g2[3].metric("現況CF", f"{row['c_bv']:.1f}" if pd.notna(row["c_bv"]) else "—")
            g2[4].metric("固都税(仮)", f"{row['c_av']:.1f}" if pd.notna(row["c_av"]) else "—")
            g2[5].metric("7年通算", f"{row['c_dq']:,.0f}" if pd.notna(row["c_dq"]) else "—")


def render_edit_form():
    """全入力項目を1つのフォームにまとめ、1回のUPDATEで保存する。"""
    structures = query("select structure from re_structure_types order by sort_order"
                       )["structure"].tolist()

    with st.form(key=f"edit_{prop['id']}"):
        st.markdown("#### 物件情報")

        c = st.columns([3, 3, 2, 2])
        f_name = c[0].text_input("物件名", txt(prop["name"]))
        f_addr = c[1].text_input("所在地", txt(prop["address"]))
        f_reply = c[2].date_input("返信日付", day(prop["reply_date"]))
        f_label = c[3].text_input("版のラベル", txt(prop["scenario_label"]),
                                  help="例：サブリース解除後／2025年版／現地確認反映")

        c = st.columns(5)
        cur_st = txt(prop["structure"])
        f_struct = c[0].selectbox("構造", structures,
                                  index=structures.index(cur_st) if cur_st in structures else 0)
        f_built = c[1].date_input("建築日", day(prop["built_date"]),
                                  min_value=datetime.date(1950, 1, 1))
        f_price = c[2].number_input("販売価格(万円)", value=num(prop["purchase_price"]), step=10.0)
        f_nego = c[3].number_input("指値後価格(万円)", value=num(prop["negotiated_price"]), step=10.0)
        f_rate = c[4].number_input("銀行提示金利", value=num(prop["bank_offered_rate"]),
                                   step=0.001, format="%.3f",
                                   help="例：0.02（2.0%）。空欄なら標準の1.5%で計算")

        c = st.columns(6)
        f_land = c[0].number_input("土地面積(㎡)", value=num(prop["land_area"]), step=1.0)
        f_floor = c[1].number_input("延床面積(㎡)", value=num(prop["floor_area"]), step=1.0)
        f_road = c[2].number_input("路線価(実)", value=num(prop["road_price_actual"]), step=0.1,
                                   help="空欄なら 1.0 として計算")
        f_zoning = c[3].text_input("用途地域", txt(prop["zoning"]))
        f_zcoef = c[4].number_input("用途地域係数", value=num(prop["zone_coef"]), step=0.05,
                                    help="商業110 近商105 住居100 準工80 工業70")
        f_scoef = c[5].number_input("土地形状係数", value=num(prop["shape_coef"]), step=0.05)

        c = st.columns(6)
        f_full = c[0].number_input("満室年収(万円)", value=num(prop["full_income"]), step=1.0)
        f_curr = c[1].number_input("現況年収(万円)", value=num(prop["current_income"]), step=1.0)
        f_tax = c[2].number_input("固都税・実(万円)", value=num(prop["property_tax"]), step=1.0,
                                  help="空欄なら建物評価から自動で仮計算")
        f_extra = c[3].number_input("EV費等の追加(万円)", value=num(prop["extra_cost"]), step=1.0,
                                    help="CATV・インターネット・浄化槽の維持費などの年額")
        f_occ = c[4].number_input("入居戸数", value=num(prop["occupied_units"]), step=1.0)
        f_total = c[5].number_input("総戸数", value=num(prop["total_units"]), step=1.0)

        c = st.columns(6)

        def pick(col, label, cur, help=None):
            v = txt(cur)
            return col.selectbox(label, ARINASHI,
                                 index=ARINASHI.index(v) if v in ARINASHI else 0, help=help)

        f_park = c[0].number_input("駐車場(台)", value=num(prop["parking_spaces"]), step=1.0)
        f_expark = c[1].text_input("敷地外駐車場", txt(prop["external_parking"]))
        f_ev = pick(c[2], "EV", prop["has_elevator"])
        f_septic = pick(c[3], "浄化槽", prop["has_septic_tank"])
        f_net = pick(c[4], "無料NET/CATV", prop["free_internet"],
                     help="元Excel AD列。ケーブルテレビ・インターネットの無料提供")
        f_hazard = c[5].text_input("ハザード", txt(prop["hazard"]),
                                   help="例：浸水／土砂災害／津波5m")

        c = st.columns([2, 2, 2])
        f_life = c[0].number_input("耐用年数の上書き(年)", value=num(prop["legal_useful_life"]),
                                   step=1.0,
                                   help=f"通常は空欄。空欄なら構造から自動で {prop['useful_life']:.0f} 年")
        f_contact = c[1].text_input("返信手段", txt(prop["contact_method"]))
        f_channel = c[2].text_input("問合せ媒体", txt(prop["inquiry_channel"]))

        st.markdown("#### メモ・コメント")
        c = st.columns(2)
        m_memo = c[0].text_area("メモ・所感・疑問", txt(prop["memo"]), height=150)
        m_input = c[1].text_area("入力メモ", txt(prop["input_memo"]), height=150,
                                 help="満室年収の計算根拠など、数値の出どころのメモ")
        c = st.columns(2)
        m_broker = c[0].text_area("仲介業者コメント", txt(prop["broker_comment"]), height=120)
        m_bank = c[1].text_area("銀行打診結果（原文）", txt(prop["bank_inquiry_result"]), height=120,
                                help="構造化された打診結果は接触履歴側で管理しています")

        saved = st.form_submit_button("すべて保存する", type="primary")

    if saved:
        execute(UPDATE_SQL, {
            "id": str(prop["id"]),
            "name": blank_to_none(f_name), "address": blank_to_none(f_addr),
            "reply_date": f_reply, "structure": f_struct,
            "built_date": f_built, "zoning": blank_to_none(f_zoning),
            "purchase_price": f_price, "negotiated_price": f_nego,
            "land_area": f_land, "floor_area": f_floor,
            "zone_coef": f_zcoef, "shape_coef": f_scoef, "road_price_actual": f_road,
            "full_income": f_full, "current_income": f_curr,
            "occupied_units": f_occ, "total_units": f_total,
            "property_tax": f_tax, "extra_cost": f_extra,
            "parking_spaces": f_park, "external_parking": blank_to_none(f_expark),
            "has_elevator": blank_to_none(f_ev), "has_septic_tank": blank_to_none(f_septic),
            "free_internet": blank_to_none(f_net), "hazard": blank_to_none(f_hazard),
            "bank_offered_rate": f_rate, "legal_useful_life": f_life,
            "scenario_label": blank_to_none(f_label),
            "memo": blank_to_none(m_memo), "input_memo": blank_to_none(m_input),
            "broker_comment": blank_to_none(m_broker),
            "bank_inquiry_result": blank_to_none(m_bank),
            "contact_method": blank_to_none(f_contact),
            "inquiry_channel": blank_to_none(f_channel),
        })
        st.success("保存しました。")
        st.rerun()

    if txt(prop["name_raw"]) and prop["name_raw"] != prop["name"]:
        st.caption(f"元Excelの物件名：{prop['name_raw']}")


def render_interactions():
    """この物件について誰と何を話したか（銀行打診・ヒアリング）を出す。"""
    hist = query("""
        select case i.kind when 'bank_inquiry' then '銀行打診'
                           when 'sales_contact' then '売買仲介'
                           when 'rental_hearing' then '賃貸ヒアリング'
                           else i.kind end as 種別,
               c.name as 会社, o.branch_name as 拠点,
               i.occurred_on as 日付,
               ip.loanable_amount as 融資可能額,
               coalesce(ip.result, i.content) as 内容
        from re_interaction_properties ip
        join re_interactions i on i.id = ip.interaction_id
        join re_offices o on o.id = i.office_id
        join re_companies c on c.id = o.company_id
        where ip.property_id = :pid
        order by i.kind, i.occurred_on desc nulls last
    """, {"pid": str(prop["id"])})

    if hist.empty:
        st.caption("この物件についての打診・ヒアリングの記録はまだありません。")
    else:
        st.dataframe(hist, width="stretch", hide_index=True,
                    column_config={
                        "融資可能額": st.column_config.NumberColumn(format="%.0f 万円"),
                        "内容": st.column_config.TextColumn(width="large"),
                    })


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
                    "販売価格":   st.column_config.NumberColumn(format="%.0f 万円"),
                    "指値後価格": st.column_config.NumberColumn(format="%.0f 万円"),
                    "満室利回":   st.column_config.NumberColumn(format="percent"),
                    "積算比率":   st.column_config.NumberColumn(format="%.2f"),
                    "実質cf":     st.column_config.NumberColumn("実質CF", format="%.1f 万円"),
                    "cf判定":     st.column_config.TextColumn("判定"),
                })


# ── 描画 ────────────────────────────────────────────────────
# 版が1つだけならタブを出さず、まるごと1画面にする。
def render_main():
    render_simulation()
    st.markdown("#### この物件について話したこと")
    render_interactions()
    render_edit_form()


if len(versions) > 1:
    main_tab, ver_tab = st.tabs(["この版", "版の比較"])
    with main_tab:
        render_main()
    with ver_tab:
        render_versions()
else:
    render_main()
