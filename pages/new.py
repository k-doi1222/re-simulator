"""物件の新規登録 — UC-1 スクリーニングの入口

月6件ほど流入する新着物件を、最小の手間で登録して即座に判定を見るための画面。
必須は「物件名」だけにしてあり、価格・面積・年収・建築日が入れば判定が出る。
"""
import datetime

import streamlit as st

from auth import require_password
from db import execute, query
from theme import compact_css

require_password()
compact_css()

st.markdown("### 物件を登録")
st.caption("判定を出すのに必要なのは 販売価格・土地面積・延床面積・建築日・満室年収 の5つです。"
          "分かる範囲で入れて登録し、あとから詳細画面で足せます。")

structures = query("select structure from re_structure_types order by sort_order")["structure"].tolist()

offices = query("""
    select o.id, c.name || '　' || coalesce(o.branch_name,'') as label
    from re_offices o join re_companies c on c.id = o.company_id
    where 'sales_broker' = any(c.kinds) or 'rental_agency' = any(c.kinds)
    order by c.name, o.branch_name
""")
office_opts = ["（未選択）"] + offices["label"].tolist()

with st.form("new_property"):
    c = st.columns([3, 3, 2, 2])
    name = c[0].text_input("物件名 *", placeholder="例：グランドコート")
    address = c[1].text_input("所在地")
    reply_date = c[2].date_input("返信日付", datetime.date.today())
    src = c[3].selectbox("紹介元", office_opts,
                         help="この物件を持ってきてくれた仲介業者")

    # 金額・面積・年収は整数、路線価は小数第1位、係数は%で入力してもらう
    c = st.columns(5)
    structure = c[0].selectbox("構造", structures)
    built_date = c[1].date_input("建築日", value=None, min_value=datetime.date(1950, 1, 1))
    price = c[2].number_input("販売価格(万円)", value=None, step=10.0, format="%.0f")
    nego = c[3].number_input("指値後価格(万円)", value=None, step=10.0, format="%.0f",
                             help="空欄なら販売価格をそのまま使います")
    rate = c[4].number_input("銀行提示金利", value=None, step=0.001, format="%.3f",
                             help="空欄なら標準の1.5%で計算")

    c = st.columns(6)
    land = c[0].number_input("土地面積(㎡)", value=None, step=1.0, format="%.0f")
    floor = c[1].number_input("延床面積(㎡)", value=None, step=1.0, format="%.0f")
    road = c[2].number_input("路線価(実)", value=None, step=0.1, format="%.1f",
                             help="空欄なら 1.0")
    zoning = c[3].text_input("用途地域")
    zcoef_pct = c[4].number_input("用途地域係数(%)", value=100, step=5, format="%d",
                                  help="商業110 近商105 住居100 準工80 工業70")
    scoef_pct = c[5].number_input("土地形状係数(%)", value=100, step=5, format="%d")

    c = st.columns(6)
    full_income = c[0].number_input("満室年収(万円)", value=None, step=1.0, format="%.0f")
    curr_income = c[1].number_input("現況年収(万円)", value=None, step=1.0, format="%.0f")
    tax = c[2].number_input("固都税・実(万円)", value=None, step=1.0, format="%.0f",
                            help="空欄なら建物評価から自動で仮計算")
    extra = c[3].number_input("EV費等の追加(万円)", value=None, step=1.0, format="%.0f",
                              help="CATV・インターネット・浄化槽の維持費などの年額")
    occ = c[4].number_input("入居戸数", value=None, step=1.0, format="%.0f")
    total = c[5].number_input("総戸数", value=None, step=1.0, format="%.0f")

    c = st.columns(6)
    park = c[0].number_input("駐車場(台)", value=None, step=1.0)
    expark = c[1].text_input("敷地外駐車場")
    ev = c[2].selectbox("EV", ["", "あり", "なし"])
    septic = c[3].selectbox("浄化槽", ["", "あり", "なし"])
    net = c[4].selectbox("無料NET/CATV", ["", "あり", "なし"])
    hazard = c[5].text_input("ハザード", placeholder="浸水 / 土砂災害 など")

    c = st.columns(2)
    memo = c[0].text_area("メモ・所感・疑問", height=110)
    broker_comment = c[1].text_area("仲介業者コメント", height=110)

    submitted = st.form_submit_button("登録する", type="primary")

if submitted:
    if not name.strip():
        st.error("物件名を入力してください。")
    else:
        office_id = None
        if src != "（未選択）":
            office_id = str(offices.loc[offices["label"] == src, "id"].iloc[0])

        def z(v):
            return None if v is None or str(v).strip() == "" else v

        new_id = query("""
            insert into re_properties (
              name, address, reply_date, structure, built_date, zoning,
              purchase_price, negotiated_price, land_area, floor_area,
              zone_coef, shape_coef, road_price_actual,
              full_income, current_income, occupied_units, total_units,
              property_tax, extra_cost, parking_spaces, external_parking,
              has_elevator, has_septic_tank, free_internet, hazard,
              bank_offered_rate, memo, broker_comment, source_office_id
            ) values (
              :name, :address, :reply_date, :structure, :built_date, :zoning,
              :price, :nego, :land, :floor,
              :zcoef, :scoef, :road,
              :full_income, :curr_income, :occ, :total,
              :tax, :extra, :park, :expark,
              :ev, :septic, :net, :hazard,
              :rate, :memo, :broker_comment, :office_id
            ) returning id
        """, {
            "name": name.strip(), "address": z(address), "reply_date": reply_date,
            "structure": structure, "built_date": built_date, "zoning": z(zoning),
            "price": price, "nego": nego, "land": land, "floor": floor,
            # 係数は画面では%で入力してもらうので、保存時に100で割って元に戻す
            "zcoef": None if zcoef_pct is None else zcoef_pct / 100,
            "scoef": None if scoef_pct is None else scoef_pct / 100,
            "road": road,
            "full_income": full_income, "curr_income": curr_income,
            "occ": occ, "total": total, "tax": tax, "extra": extra,
            "park": park, "expark": z(expark), "ev": z(ev), "septic": z(septic),
            "net": z(net), "hazard": z(hazard), "rate": rate,
            "memo": z(memo), "broker_comment": z(broker_comment), "office_id": office_id,
        })
        query.clear()
        st.session_state["selected_id"] = str(new_id.iloc[0]["id"])
        st.success(f"「{name}」を登録しました。詳細画面へ移動します。")
        st.switch_page("pages/detail.py")
