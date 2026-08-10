"""パスワードによる入口の門。

クラウドに公開している側は、閲覧者が本人と不動産アドバイザーの2名なので
共通のパスワードを1つ置いている。DBには担当者の実名・電話や、売主の相続事情といった
第三者の情報が入っているため、公開URLをパスワードなしにはしない。

手元で動かすときは不要なので、`.streamlit/secrets.toml` の password を
空文字にしておけば素通りする。この設定ファイルはGitに載らないので、
「手元は空・クラウドは設定あり」を同じコードのまま使い分けられる。

なお **設定そのものが無いときは通さない**（素通りさせない）。
クラウド側で設定を入れ忘れたときに、気づかないまま公開されてしまうのを防ぐため。
"""
from __future__ import annotations

import hmac

import streamlit as st


def _configured_password() -> str | None:
    """設定されたパスワードを返す。設定自体が無ければ None。"""
    try:
        return str(st.secrets["app"]["password"])
    except Exception:
        return None


def require_password() -> None:
    """未認証ならログイン画面を出して以降の処理を止める。"""
    expected = _configured_password()

    if expected is None:
        st.error("パスワードが設定されていません。")
        st.caption("`.streamlit/secrets.toml`（クラウドの場合は Settings → Secrets）に "
                   "`[app] password` を設定してください。"
                   "手元で認証を省きたい場合は、空文字 `password = \"\"` を明示的に設定します。")
        st.stop()

    if expected == "":          # 意図的に空にしてある＝認証なしで使う
        return
    if st.session_state.get("authenticated"):
        return

    st.markdown("### 不動産投資シミュレーション")
    st.caption("閲覧にはパスワードが必要です。")

    with st.form("login", border=False):
        pw = st.text_input("パスワード", type="password", label_visibility="collapsed",
                           placeholder="パスワード")
        ok = st.form_submit_button("ログイン", type="primary", width="stretch")

    if ok:
        # hmac.compare_digest で総当たりの手がかり（応答時間の差）を与えない
        if hmac.compare_digest(pw, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")

    st.stop()
