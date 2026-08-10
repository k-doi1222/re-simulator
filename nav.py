"""画面どうしの行き来。

表の行を選ぶと関連する画面へ飛べるようにするための小道具。
Streamlit の表はセルにリンクを埋められないので、「行を選ぶ → 対応する画面へ移動」で実現する。
"""
import streamlit as st

PROPERTY_PAGE = "pages/detail.py"


def goto_property(property_id: str) -> None:
    """物件詳細へ移動する。一覧側の選択状態は捨てて、戻ったときに再発火しないようにする。"""
    st.session_state["selected_id"] = str(property_id)
    st.session_state.pop("property_table", None)
    st.switch_page(PROPERTY_PAGE)


# 取引先の種別 → その画面
OFFICE_PAGE = {
    "bank": "pages/banks.py",
    "sales_broker": "pages/sales_brokers.py",
    "rental_agency": "pages/rental_agencies.py",
}


def goto_office_edit(office_id: str, company_kind: str,
                     back_property_id: str | None = None) -> None:
    """取引先の画面へ、その拠点のカルテを開いた状態で移動する。

    back_property_id を渡しておくと、直し終えたあと元の物件詳細へ戻れる。
    """
    st.session_state["edit_office_id"] = str(office_id)
    st.session_state["edit_office_kind"] = company_kind
    st.session_state["edit_office_back"] = (str(back_property_id)
                                            if back_property_id else None)
    st.switch_page(OFFICE_PAGE[company_kind])


def take_office_edit(company_kind: str) -> str | None:
    """「直しに来た」拠点IDを返す。飛んだ先の種別のページでだけ効く。

    1社が銀行と売買仲介を兼ねている実データがあり（三十三銀行）、
    種別で絞らないと、銀行から飛んだ指定が売買仲介の画面にも効いてしまう。
    """
    if st.session_state.get("edit_office_kind") != company_kind:
        return None
    return st.session_state.get("edit_office_id")


def render_back_to_property(company_kind: str) -> None:
    """物件詳細から飛んできたときだけ、戻るボタンを出す。"""
    if st.session_state.get("edit_office_kind") != company_kind:
        return
    back = st.session_state.get("edit_office_back")
    if not back:
        return
    if st.button("← 物件詳細に戻る", key="back_to_prop"):
        for k in ("edit_office_id", "edit_office_kind", "edit_office_back"):
            st.session_state.pop(k, None)
        goto_property(back)
