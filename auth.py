"""単一パスワードによる入口の門。

閲覧者は本人と不動産アドバイザーの2名のみ。アカウント管理はしない。
DBには担当者の実名・電話や、売主の相続事情といった第三者の情報が入っているため、
パスワードなしでの公開はしない。
"""
from __future__ import annotations

import hmac

import streamlit as st


def require_password() -> None:
    """未認証ならログイン画面を出して以降の処理を止める。"""
    if st.session_state.get("authenticated"):
        return

    st.markdown("### 不動産投資シミュレーション")
    st.caption("閲覧にはパスワードが必要です。")

    with st.form("login", border=False):
        pw = st.text_input("パスワード", type="password", label_visibility="collapsed",
                           placeholder="パスワード")
        ok = st.form_submit_button("ログイン", type="primary", use_container_width=True)

    if ok:
        # hmac.compare_digest で総当たりの手がかり（応答時間の差）を与えない
        if hmac.compare_digest(pw, st.secrets["app"]["password"]):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")

    st.stop()
