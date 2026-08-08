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


def goto_office(office_id: str, page: str) -> None:
    """取引先の画面へ、対象の拠点を選んだ状態で移動する。"""
    st.session_state["selected_office_id"] = str(office_id)
    st.switch_page(page)


def jump_on_select(event, df, id_col, on_pick) -> None:
    """表で行が選ばれていたら on_pick(その行) を呼ぶ。

    event は st.dataframe(on_select="rerun") の戻り値。
    """
    rows = getattr(event, "selection", None)
    rows = getattr(rows, "rows", None) if rows else None
    if rows:
        picked = df.iloc[rows[0]]
        if picked.get(id_col) is not None:
            on_pick(picked)
