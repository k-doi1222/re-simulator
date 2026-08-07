"""画面共通の見た目の設定。

入力値（人が入れた値）と計算値（入力値から都度算出される値）を、
背景色で見分けられるようにするための色をここで一元管理する。
"""
import streamlit as st

# 自動計算値の背景色。config.toml の secondaryBackgroundColor と調和する薄い青。
CALC_BG = "#EEF3FC"


def compact_css() -> None:
    """1画面に情報を詰めるための共通スタイル。各ページの先頭で呼ぶ。"""
    st.html(f"""
    <style>
      .st-key-calc_block, .st-key-solver_block, .st-key-detail_calc {{
          background-color: {CALC_BG};
          border-radius: 0.4rem;
          padding: 0.5rem 0.8rem;
      }}
      .block-container {{ padding-top: 2.5rem; padding-bottom: 2rem; }}
      div[data-testid="stMetricValue"] {{ font-size: 1.35rem; }}
      div[data-testid="stMetricLabel"] {{ font-size: 0.78rem; }}
      div[data-testid="stVerticalBlock"] {{ gap: 0.5rem; }}
      h4 {{ margin-top: 0.4rem; margin-bottom: 0; }}
    </style>
    """)


def calc_style(df, calc_cols):
    """計算値の列だけ背景色を塗った Styler を返す。存在しない列は無視する。"""
    cols = [c for c in calc_cols if c in df.columns]
    return df.style.set_properties(subset=cols, **{"background-color": CALC_BG}) if cols else df
