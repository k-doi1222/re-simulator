"""画面共通の見た目と、表の列書式。

- 入力値（人が入れた値）と計算値（入力値から都度算出される値）を背景色で見分ける
- 単位の書式を全画面で統一する（万円は整数、比率は%）
"""
import streamlit as st

# 自動計算値の背景色。config.toml の secondaryBackgroundColor と調和する薄い青。
CALC_BG = "#EEF3FC"

# 一覧の表の高さ。画面の高さから見出し・絞り込み欄の分を引いて残り全部を使う。
# Streamlitはサーバー側で動くのでブラウザの高さを知れない。CSSの vh で追従させる。
TABLE_VH = "calc(100vh - 250px)"


def compact_css(table_vh: str = TABLE_VH) -> None:
    """1画面に情報を詰めるための共通スタイル。各ページの先頭で呼ぶ。"""
    st.html(f"""
    <style>
      .st-key-calc_block, .st-key-solver_block, .st-key-detail_calc {{
          background-color: {CALC_BG};
          border-radius: 0.4rem;
          padding: 0.5rem 0.8rem;
      }}
      /* 上余白はStreamlitのヘッダー(56px)より広く取ること。
         詰めすぎると画面上部の要素がヘッダーの下に隠れる（基準文字サイズ15pxで4.5rem=67.5px）。 */
      .block-container {{ padding-top: 4.5rem; padding-bottom: 1rem; }}
      div[data-testid="stMetricValue"] {{ font-size: 1.35rem; }}
      div[data-testid="stMetricLabel"] {{ font-size: 0.78rem; }}
      div[data-testid="stVerticalBlock"] {{ gap: 0.5rem; }}
      h4 {{ margin-top: 0.4rem; margin-bottom: 0; }}

      /* 一覧の表を画面下端まで伸ばす。
         ページ側で key="fulltable..." の枠に入れた表が対象（前方一致で拾う）。 */
      div[class*="st-key-fulltable"] div[data-testid="stDataFrameResizable"] {{
          height: {table_vh} !important;
          min-height: 320px;
      }}

      /* 目標判定に乗せる価格のカード。st.metric の見た目に合わせ、
         灰色の丸いタグ（指値率・指値幅）を2つ横に並べる。 */
      .tp {{ padding: 0.1rem 0 0.4rem 0; }}
      .tp-lab {{ font-size: 0.78rem; color: #666; }}
      .tp-val {{ font-size: 1.35rem; font-weight: 600; line-height: 1.5; }}
      /* 目安として出す値は控えめに。主役（判定・価格・築年数・積算比率）を立たせる。 */
      .tp-lab-sub {{ font-size: 0.72rem; color: #999; }}
      .tp-val-sub {{ font-size: 1.0rem; font-weight: 500; line-height: 1.5; color: #555; }}
      /* タグがない項目でも高さが揃うよう、空でも最低限の高さを確保する */
      .tp-pills {{ display: flex; gap: 0.3rem; flex-wrap: wrap; margin-top: 0.15rem;
                   min-height: 1.25rem; }}
      .tp-pill {{
          background: rgba(128,128,128,0.15);
          border-radius: 999px;
          padding: 0.1rem 0.55rem;
          font-size: 0.78rem;
          color: #444;
          white-space: nowrap;
      }}
      .tp-pill-sub {{ font-size: 0.72rem; color: #666; background: rgba(128,128,128,0.10); }}
    </style>
    """)


def calc_style(df, calc_cols):
    """計算値の列だけ背景色を塗った Styler を返す。存在しない列は無視する。"""
    cols = [c for c in calc_cols if c in df.columns]
    return df.style.set_properties(subset=cols, **{"background-color": CALC_BG}) if cols else df


# ── 列の書式（全画面で共通に使う）──────────────────────────
def money(label=None, **kw):
    """万円単位の金額。小数点以下は出さない。"""
    return st.column_config.NumberColumn(label, format="%d 万円", **kw)


def ratio(label=None, **kw):
    """比率。0.107 → 10.7% のように%で表示する。"""
    return st.column_config.NumberColumn(label, format="percent", **kw)


def count(label=None, unit="", **kw):
    """件数・年数などの整数。"""
    return st.column_config.NumberColumn(label, format=f"%d{unit}", **kw)


def longtext(label=None, **kw):
    return st.column_config.TextColumn(label, width="large", **kw)
