"""Supabase(PostgreSQL) への接続。

Streamlit はサーバー側で動くため、接続情報がブラウザに渡ることはない。
そのため RLS ポリシーに頼らず、通常の接続で読み書きする方針。
接続情報は .streamlit/secrets.toml に置き、Git には載せない（.gitignore 済み）。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text


@st.cache_resource
def get_engine():
    """接続はアプリ全体で1つを使い回す（再実行のたびに繋ぎ直さない）。"""
    url = st.secrets["db"]["url"]
    return create_engine(
        url,
        pool_pre_ping=True,   # 切れたコネクションを掴まないようにする
        pool_recycle=1800,
    )


@st.cache_data(ttl=300, show_spinner=False)
def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """SELECT を実行して DataFrame で返す。同じSQLは5分間キャッシュする。"""
    with get_engine().connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or {})


def execute(sql: str, params: dict | None = None) -> None:
    """INSERT / UPDATE を実行する。実行後はキャッシュを捨てて再取得させる。"""
    with get_engine().begin() as conn:
        conn.execute(text(sql), params or {})
    query.clear()


def refresh_calc_cache() -> int:
    """一覧に出す計算値のうち、古くなったものだけ計算し直す。

    `re_properties_v` は計算関数を毎回呼ばず `re_property_calc_cache` を読む。
    そのため、表示する前にこれを呼んで最新にしておく。

    計算し直すのは「入力値が変わった物件」と「築年数が変わった物件」だけなので、
    ふだんは0件で一瞬（実測0.05秒）で返る。0件のときは Streamlit 側のキャッシュを
    捨てない（毎回捨てると画面の再描画がそのたび遅くなるため）。
    """
    with get_engine().begin() as conn:
        n = conn.execute(text("select re_refresh_calc_cache()")).scalar_one()
    if n:
        query.clear()
    return int(n or 0)


def check_connection() -> tuple[bool, str]:
    """接続確認。成功なら (True, バージョン文字列)、失敗なら (False, エラー内容)。"""
    try:
        with get_engine().connect() as conn:
            v = conn.execute(text("select version()")).scalar_one()
        return True, v
    except Exception as e:  # noqa: BLE001  接続失敗の理由をそのまま画面に出したい
        return False, str(e)
