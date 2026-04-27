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
DB_PATH  = BASE_DIR / 'data' / 'stocks.db'

st.set_page_config(
    page_title="台灣法人追蹤",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 自定義樣式 ────────────────────────────────────────────
st.markdown("""
<style>
/* 全域字體與背景 */
html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }

/* 說明卡片 */
.guide-card {
    background: linear-gradient(135deg, #1e3a5f 0%, #0f2744 100%);
    border-radius: 12px;
    padding: 20px 28px;
    margin-bottom: 24px;
    color: #e8f0fe;
    border-left: 4px solid #4a9eff;
}
.guide-card h3 { color: #ffffff; margin: 0 0 12px 0; font-size: 1.1rem; }
.guide-card p  { margin: 6px 0; font-size: 0.92rem; line-height: 1.6; }
.guide-card .tag-red    { color: #ff6b6b; font-weight: 600; }
.guide-card .tag-yellow { color: #ffd93d; font-weight: 600; }
.guide-card .tag-grey   { color: #aaaaaa; font-weight: 600; }
.guide-card .warning    { color: #adc8ff; font-size: 0.82rem; margin-top: 12px; }

/* 訊號區塊標題 */
.signal-header-red    { background:#3d1515; border-left:4px solid #ff4d4d;
                         padding:8px 16px; border-radius:6px; margin:16px 0 8px;
                         color:#ffffff !important; }
.signal-header-red  * { color:#ffffff !important; }
.signal-header-yellow { background:#3d3000; border-left:4px solid #ffd93d;
                         padding:8px 16px; border-radius:6px; margin:16px 0 8px;
                         color:#ffffff !important; }
.signal-header-yellow * { color:#ffffff !important; }

/* 資料來源標注 */
.source-bar {
    background: #0d1117;
    border: 1px solid #2d4a2d;
    border-radius: 8px;
    padding: 10px 16px;
    margin: 4px 0 16px;
    font-size: 0.82rem;
    color: #7ec87e;
}
.source-bar a { color: #4a9eff; text-decoration: none; }
.source-bar a:hover { text-decoration: underline; }

/* 個股研判卡 */
.judge-card {
    background: #1a1a2e;
    border: 1px solid #2d2d4e;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 12px 0;
}
.judge-card h4 { color: #adc8ff; margin: 0 0 8px 0; font-size: 0.9rem; }
.judge-badge {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 0.82rem;
    font-weight: 600;
    margin-right: 8px;
}
.badge-strong { background:#4d0000; color:#ff8080; border:1px solid #ff4d4d; }
.badge-medium { background:#3d2e00; color:#ffd93d; border:1px solid #ffd93d; }
.badge-watch  { background:#1e1e1e; color:#888888; border:1px solid #555; }
.judge-note   { color: #8899bb; font-size: 0.82rem; margin-top: 8px; }
</style>
""", unsafe_allow_html=True)

DISPLAY_COLS = {
    'stock_id':       '代號',
    'stock_name':     '名稱',
    'foreign_net':    '外資（張）',
    'trust_net':      '投信（張）',
    'dealer_net':     '自營（張）',
    'total_net':      '三大合計（張）',
    'foreign_consec': '外資連買天數',
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
        st.info("📅 資料累積中，需要 2 個交易日以上才會顯示趨勢圖")
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(x=hist['date'], y=hist['foreign_net'], name='外資',  marker_color='#4a9eff'))
    fig.add_trace(go.Bar(x=hist['date'], y=hist['trust_net'],   name='投信',  marker_color='#ffd93d'))
    fig.add_trace(go.Bar(x=hist['date'], y=hist['dealer_net'],  name='自營商', marker_color='#6bcb77'))
    fig.add_trace(go.Scatter(
        x=hist['date'], y=hist['total_net'],
        name='三大合計', mode='lines+markers',
        line=dict(color='#ff6b6b', width=2)
    ))
    fig.update_layout(
        title=f"{stock_id} {stock_name} — 法人買賣超走勢（張）",
        barmode='group',
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#cccccc'),
        legend=dict(orientation='h', y=1.12, bgcolor='rgba(0,0,0,0)'),
        margin=dict(t=60, b=20),
        height=360,
    )
    fig.update_xaxes(gridcolor='#2d2d2d')
    fig.update_yaxes(gridcolor='#2d2d2d')
    st.plotly_chart(fig, use_container_width=True)


def render_pie(row: pd.Series):
    """三大法人買超佔比圓餅圖（僅顯示正數方，即實際買超方）"""
    labels, values, colors = [], [], []
    mapping = [
        ('外資', float(row['foreign_net']), '#4a9eff'),
        ('投信', float(row['trust_net']),   '#ffd93d'),
        ('自營商', float(row['dealer_net']), '#6bcb77'),
    ]
    for name, val, color in mapping:
        if val > 0:
            labels.append(name)
            values.append(val)
            colors.append(color)

    if not labels:
        st.caption("今日三大法人均為賣超，無正向買超佔比可顯示。")
        return

    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        marker=dict(colors=colors),
        hole=0.45,
        textinfo='label+percent',
        textfont=dict(size=13),
    ))
    fig.update_layout(
        title="各法人買超佔比（僅計買超方）",
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#cccccc'),
        margin=dict(t=50, b=10, l=10, r=10),
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("⚠️ TWSE 僅揭露外資、投信、自營商三大類別合計，無法細分至個別機構名稱（如哪家外資公司）。")


def render_judge(row: pd.Series, date: str):
    """個股系統研判區塊（目前為觀察期 placeholder，M5 後升級為建議）"""
    strength = row['signal_strength']
    badge_class = {'strong': 'badge-strong', 'medium': 'badge-medium', 'watch': 'badge-watch'}.get(strength, 'badge-watch')
    badge_label = {'strong': '🔴 強訊號', 'medium': '🟡 中訊號', 'watch': '⚪ 觀察中'}.get(strength, '')

    f_consec = int(row['foreign_consec'])
    cross    = bool(row['cross_buy'])
    all3     = bool(row['all_three_buy'])

    obs = []
    if all3:
        obs.append("三大法人同日買超（最強交叉確認）")
    elif cross:
        obs.append("外資 + 投信同日買超")
    if f_consec >= 3:
        obs.append(f"外資連續買超 {f_consec} 日")

    obs_text = "、".join(obs) if obs else "單邊法人買超"

    st.markdown(f"""
<div class="judge-card">
  <h4>📋 系統研判</h4>
  <span class="judge-badge {badge_class}">{badge_label}</span>
  <p style="color:#ccc; font-size:0.88rem; margin:10px 0 4px;">觀察到：{obs_text}</p>
  <p class="judge-note">
    ⚠️ 目前處於資料累積期（第 1 週），進出場建議功能將在累積 4 週歷史資料、準確率驗證達標後啟用。<br>
    建議搭配 <a href="https://www.twse.com.tw/rwd/zh/fund/T86?date={date.replace('-','')}&selectType=ALLBUT0999&response=json"
    target="_blank" style="color:#4a9eff;">TWSE 原始資料</a> 自行判斷。
  </p>
</div>
""", unsafe_allow_html=True)


# ── 主畫面 ───────────────────────────────────────────────

st.title("📊 台灣股市法人追蹤")

df, date = load_signals()

if df is None or df.empty:
    st.warning("⏳ 尚無資料，每週一至週五 17:30 後自動更新，請稍後再來。")
    st.stop()

# ── 使用說明 ─────────────────────────────────────────────
st.markdown(f"""
<div class="guide-card">
  <h3>📖 這工具在看什麼？怎麼用？</h3>
  <p>台灣股市有三種大型機構投資人（法人）：<strong>外資</strong>（外國大基金）、
  <strong>投信</strong>（台灣本土基金）、<strong>自營商</strong>（券商自己的錢）。
  他們掌握大量資金與內部研究，動作往往早於一般散戶。</p>
  <p><strong>篩選邏輯：</strong>每天自動掃描台灣全市場約 1,300 支上市個股，
  抓取三大法人當日的買賣超張數，計算哪些個股同時被多家法人買進，
  再根據強度分級顯示：</p>
  <p>
    <span class="tag-red">🔴 強訊號</span> 外資＋投信＋自營商三方同日買超，或外資連續買超 5 天以上 → 最優先關注<br>
    <span class="tag-yellow">🟡 中訊號</span> 外資＋投信同一天都在買，或外資連買 3 天以上 → 值得追蹤<br>
    <span class="tag-grey">⚪ 觀察中</span> 單邊法人買超（僅外資或僅投信）→ 列入候補清單
  </p>
  <p><strong>怎麼用：</strong>看訊號清單 → 選感興趣的個股 → 下方查看法人走勢圖與買超佔比 → 點連結核對 TWSE 官方原始資料。</p>
  <p class="warning">⚠️ 此工具為決策輔助，非買賣指令。注意：外資／投信／自營商為類別合計，TWSE 不揭露個別機構名稱。資料 T+1，每個交易日 17:30 後自動更新。</p>
</div>
""", unsafe_allow_html=True)

date_str_raw = date.replace('-', '')
st.markdown(f"""
<div class="source-bar">
  ✅ <strong>資料來源：台灣證券交易所（TWSE）</strong>　官方三大法人買賣超統計（T86 報表）　｜　資料日期：{date}　｜　每個交易日 17:30 後更新
  　<a href="https://www.twse.com.tw/zh/fund/T86.html" target="_blank">→ TWSE 官方查詢頁</a>
  　<a href="https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str_raw}&selectType=ALLBUT0999&response=json" target="_blank">→ 本日原始 JSON</a>
</div>
""", unsafe_allow_html=True)

# ── 計數卡 ───────────────────────────────────────────────
strong = df[df['signal_strength'] == 'strong']
medium = df[df['signal_strength'] == 'medium']
watch  = df[df['signal_strength'] == 'watch']

c1, c2, c3 = st.columns(3)
c1.metric("🔴 強訊號", f"{len(strong)} 檔", help="三大法人齊買 或 外資連買 ≥5 日")
c2.metric("🟡 中訊號", f"{len(medium)} 檔", help="外資+投信同買 或 外資連買 ≥3 日")
c3.metric("⚪ 觀察中", f"{len(watch)} 檔",  help="外資或投信單邊買超")

# ── 訊號清單 ─────────────────────────────────────────────
if not strong.empty:
    st.markdown('<div class="signal-header-red"><strong>🔴 強訊號</strong>　三大法人齊買 或 外資連買 ≥5 日</div>', unsafe_allow_html=True)
    render_table(strong)

if not medium.empty:
    st.markdown('<div class="signal-header-yellow"><strong>🟡 中訊號</strong>　外資 + 投信同日買超</div>', unsafe_allow_html=True)
    render_table(medium)

if not watch.empty:
    with st.expander(f"⚪ 觀察中（{len(watch)} 檔）"):
        render_table(watch)

# ── 個股詳細 ─────────────────────────────────────────────
st.divider()
st.subheader("🔍 個股詳細查詢")

options = df.apply(lambda r: f"{r['stock_id']}  {r['stock_name']}", axis=1).tolist()
selected = st.selectbox("選擇個股", options, label_visibility="collapsed")

if selected:
    parts      = selected.split()
    stock_id   = parts[0]
    stock_name = parts[1] if len(parts) > 1 else ''
    row        = df[df['stock_id'] == stock_id].iloc[0]

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("外資買賣超",  f"{int(row['foreign_net']):,} 張")
    col_b.metric("投信買賣超",  f"{int(row['trust_net']):,} 張")
    col_c.metric("自營商買賣超", f"{int(row['dealer_net']):,} 張")
    col_d.metric("外資連買天數", f"{int(row['foreign_consec'])} 日")

    chart_col, pie_col = st.columns([2, 1])
    with chart_col:
        render_chart(stock_id, stock_name)
    with pie_col:
        render_pie(row)

    render_judge(row, date)
