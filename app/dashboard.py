"""
台灣股市法人追蹤 — Streamlit Dashboard
每日 17:30 後自動更新（GitHub Actions 抓資料後 commit 回 repo）
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / 'data' / 'stocks.db'

st.set_page_config(
    page_title="台灣法人追蹤",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DISPLAY_COLS = {
    'stock_id':      '代號',
    'stock_name':    '名稱',
    'foreign_net':   '外資（張）',
    'trust_net':     '投信（張）',
    'dealer_net':    '自營（張）',
    'total_net':     '三大合計（張）',
    'foreign_consec':'外資連買天數',
}


@st.cache_data(ttl=300)
def load_signals(date: str = None):
    if not DB_PATH.exists():
        return pd.DataFrame(), None
    conn = sqlite3.connect(DB_PATH)
    if date is None:
        row = conn.execute("SELECT MAX(date) FROM signals").fetchone()
        date = row[0] if row and row[0] else None
    if not date:
        conn.close()
        return pd.DataFrame(), None
    df = pd.read_sql_query(
        "SELECT * FROM signals WHERE date=? ORDER BY total_net DESC",
        conn, params=(date,)
    )
    conn.close()
    return df, date


@st.cache_data(ttl=300)
def load_history(stock_id: str):
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT date, foreign_net, trust_net, dealer_net, total_net
           FROM institutional_data WHERE stock_id=? ORDER BY date""",
        conn, params=(stock_id,)
    )
    conn.close()
    return df


def render_table(sub_df: pd.DataFrame):
    display = sub_df[list(DISPLAY_COLS.keys())].rename(columns=DISPLAY_COLS)
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_chart(stock_id: str, stock_name: str):
    hist = load_history(stock_id)
    if hist.empty:
        st.info("尚無歷史資料")
        return
    if len(hist) == 1:
        st.info("歷史資料累積中，圖表需要 2 個交易日以上才能顯示趨勢")
        st.dataframe(hist, use_container_width=True, hide_index=True)
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(x=hist['date'], y=hist['foreign_net'], name='外資', marker_color='#2563EB'))
    fig.add_trace(go.Bar(x=hist['date'], y=hist['trust_net'],   name='投信', marker_color='#F59E0B'))
    fig.add_trace(go.Bar(x=hist['date'], y=hist['dealer_net'],  name='自營商', marker_color='#10B981'))
    fig.add_trace(go.Scatter(
        x=hist['date'], y=hist['total_net'],
        name='三大合計', mode='lines+markers',
        line=dict(color='#EF4444', width=2)
    ))
    fig.update_layout(
        title=f"{stock_id} {stock_name} — 近期法人買賣超（張）",
        barmode='group',
        legend=dict(orientation='h', y=1.1),
        margin=dict(t=60, b=20),
        height=380,
    )
    st.plotly_chart(fig, use_container_width=True)


# ── 主畫面 ──────────────────────────────────────────────

st.title("📊 台灣股市法人追蹤")

df, date = load_signals()

if df is None or df.empty:
    st.warning("⏳ 尚無資料。每週一至週五 17:30 後自動更新，請稍後再來。")
    st.stop()

st.caption(f"資料日期：**{date}**　｜　資料來源：台灣證券交易所（T+1）")
st.divider()

strong = df[df['signal_strength'] == 'strong']
medium = df[df['signal_strength'] == 'medium']
watch  = df[df['signal_strength'] == 'watch']

c1, c2, c3 = st.columns(3)
c1.metric("🔴 強訊號", f"{len(strong)} 檔", help="三大法人齊買 或 外資連買 ≥5 日")
c2.metric("🟡 中訊號", f"{len(medium)} 檔", help="外資+投信同買 或 外資連買 ≥3 日")
c3.metric("⚪ 觀察中", f"{len(watch)} 檔",  help="外資或投信單邊買超")

if not strong.empty:
    st.subheader("🔴 強訊號")
    render_table(strong)

if not medium.empty:
    st.subheader("🟡 中訊號（外資 + 投信同買）")
    render_table(medium)

if not watch.empty:
    with st.expander(f"⚪ 觀察中（{len(watch)} 檔）"):
        render_table(watch)

# ── 個股詳細 ──────────────────────────────────────────────

st.divider()
st.subheader("🔍 個股詳細查詢")

options = df.apply(lambda r: f"{r['stock_id']}  {r['stock_name']}", axis=1).tolist()
selected = st.selectbox("選擇個股", options, label_visibility="collapsed")

if selected:
    parts = selected.split()
    stock_id   = parts[0]
    stock_name = parts[1] if len(parts) > 1 else ''
    row = df[df['stock_id'] == stock_id].iloc[0]

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("外資買賣超", f"{row['foreign_net']:,} 張")
    col_b.metric("投信買賣超", f"{row['trust_net']:,} 張")
    col_c.metric("自營商買賣超", f"{row['dealer_net']:,} 張")
    col_d.metric("外資連買天數", f"{row['foreign_consec']} 日")

    render_chart(stock_id, stock_name)

    date_str = date.replace('-', '')
    st.markdown(
        f"[🔗 TWSE 三大法人原始資料（{date}）](https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={date_str}&selectType=ALLBUT0999&response=json)"
    )
