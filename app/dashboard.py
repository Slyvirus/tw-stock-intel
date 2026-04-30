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

/* 賣超訊號標題 */
.signal-header-blue { background:#0d1f3c; border-left:4px solid #4a9eff;
                       padding:8px 16px; border-radius:6px; margin:16px 0 8px;
                       color:#ffffff !important; }
.signal-header-blue * { color:#ffffff !important; }
.signal-header-cyan { background:#0d2a2a; border-left:4px solid #22d3ee;
                       padding:8px 16px; border-radius:6px; margin:16px 0 8px;
                       color:#ffffff !important; }
.signal-header-cyan * { color:#ffffff !important; }

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
    'stock_id':            '代號',
    'stock_name':          '名稱',
    'foreign_net':         '外資（張）',
    'trust_net':           '投信（張）',
    'dealer_net':          '自營（張）',
    'total_net':           '三大合計（張）',
    'institutional_ratio': '法人參與率',
    'foreign_consec':      '外資連買天數',
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




def fmt_ratio(val) -> str:
    """格式化法人參與率，>=50% 加 🔥 標示"""
    try:
        if val is None or (isinstance(val, float) and __import__('math').isnan(val)):
            return '-'
        pct = float(val) * 100
        return f'🔥 {pct:.1f}%' if pct >= 50 else f'{pct:.1f}%'
    except Exception:
        return '-'

def render_table(sub_df: pd.DataFrame):
    cols    = [c for c in DISPLAY_COLS.keys() if c in sub_df.columns]
    display = sub_df[cols].rename(columns=DISPLAY_COLS)
    if '法人參與率' in display.columns:
        display['法人參與率'] = display['法人參與率'].apply(fmt_ratio)
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
    """三大法人買超或賣超佔比圓餅圖"""
    f, t, d = float(row['foreign_net']), float(row['trust_net']), float(row['dealer_net'])

    # 判斷顯示買超佔比還是賣超佔比
    buy_total  = sum(v for v in [f, t, d] if v > 0)
    sell_total = sum(abs(v) for v in [f, t, d] if v < 0)

    if buy_total >= sell_total:
        # 以買超為主，顯示買超方佔比
        mapping = [('外資', f, '#4a9eff'), ('投信', t, '#ffd93d'), ('自營商', d, '#6bcb77')]
        labels = [n for n, v, _ in mapping if v > 0]
        values = [v for _, v, _ in mapping if v > 0]
        colors = [c for _, v, c in mapping if v > 0]
        title  = "各法人買超佔比"
    else:
        # 以賣超為主，顯示賣超方佔比（取絕對值）
        mapping = [('外資', f, '#4a9eff'), ('投信', t, '#22d3ee'), ('自營商', d, '#6bcb77')]
        labels = [n for n, v, _ in mapping if v < 0]
        values = [abs(v) for _, v, _ in mapping if v < 0]
        colors = [c for _, v, c in mapping if v < 0]
        title  = "各法人賣超佔比"

    if not labels:
        st.caption("今日三大法人買賣超均為 0，無佔比可顯示。")
        return

    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        marker=dict(colors=colors),
        hole=0.45,
        textinfo='label+percent',
        textfont=dict(size=13),
    ))
    fig.update_layout(
        title=title,
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#cccccc'),
        margin=dict(t=50, b=10, l=10, r=10),
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("⚠️ TWSE 僅揭露三大類別合計，無法細分至個別機構名稱。")


def render_judge(row: pd.Series, date: str):
    """個股系統研判區塊（買超或賣超均適用）"""
    buy_str  = row.get('signal_strength', None)
    sell_str = row.get('sell_strength', None)

    badges_html = ''
    obs = []

    # 買超訊號
    if pd.notna(buy_str) and buy_str:
        buy_badge = {'strong': ('badge-strong', '🔴 強買訊號'),
                     'medium': ('badge-medium', '🟡 中買訊號'),
                     'watch':  ('badge-watch',  '⚪ 買超觀察')}.get(buy_str, ('badge-watch', ''))
        badges_html += f'<span class="judge-badge {buy_badge[0]}">{buy_badge[1]}</span> '

        if bool(row.get('all_three_buy', 0)):
            obs.append("三大法人同日買超")
        elif bool(row.get('cross_buy', 0)):
            obs.append("外資＋投信同日買超")
        f_c = int(row.get('foreign_consec', 0))
        if f_c >= 3:
            obs.append(f"外資連續買超 {f_c} 日")

    # 賣超訊號
    if pd.notna(sell_str) and sell_str:
        sell_badge = {'strong': ('🔵 強賣訊號', '#4a9eff'),
                      'medium': ('🔷 中賣訊號', '#22d3ee'),
                      'watch':  ('○ 賣超觀察', '#666666')}.get(sell_str, ('', '#666'))
        badges_html += (f'<span class="judge-badge" style="background:#0d1f3c;'
                        f'color:{sell_badge[1]};border:1px solid {sell_badge[1]};">'
                        f'{sell_badge[0]}</span>')

        if bool(row.get('all_three_sell', 0)):
            obs.append("三大法人同日賣超")
        elif bool(row.get('cross_sell', 0)):
            obs.append("外資＋投信同日賣超")
        f_sc = int(row.get('foreign_sell_consec', 0))
        if f_sc >= 3:
            obs.append(f"外資連續賣超 {f_sc} 日")

    obs_text = "、".join(obs) if obs else "單邊法人動作"

    st.markdown(f"""
<div class="judge-card">
  <h4>📋 系統研判</h4>
  {badges_html}
  <p style="color:#ccc; font-size:0.88rem; margin:10px 0 4px;">觀察到：{obs_text}</p>
  <p class="judge-note">
    ⚠️ 目前處於資料累積期，進出場建議將在 4 週資料驗證後啟用。<br>
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

date_str_raw = date.replace('-', '')
st.markdown(f"""
<div class="source-bar">
  ✅ <strong>資料來源：台灣證券交易所（TWSE）</strong>　官方三大法人買賣超統計（T86 報表）　｜　資料日期：{date}　｜　每個交易日 17:30 後更新
  　<a href="https://www.twse.com.tw/zh/fund/T86.html" target="_blank">→ TWSE 官方查詢頁</a>
  　<a href="https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str_raw}&selectType=ALLBUT0999&response=json" target="_blank">→ 本日原始 JSON</a>
</div>
""", unsafe_allow_html=True)

# ── 計數卡 ───────────────────────────────────────────────
strong    = df[df['signal_strength'] == 'strong']
medium    = df[df['signal_strength'] == 'medium']
watch     = df[df['signal_strength'] == 'watch']
sell_s    = df[df['sell_strength']   == 'strong']
sell_m    = df[df['sell_strength']   == 'medium']
sell_w    = df[df['sell_strength']   == 'watch']

st.caption("**▲ 買超訊號**")
c1, c2, c3 = st.columns(3)
c1.metric("🔴 強買訊號", f"{len(strong)} 檔", help="三大法人齊買 或 外資連買 ≥5 日")
c2.metric("🟡 中買訊號", f"{len(medium)} 檔", help="外資+投信同買 或 外資連買 ≥3 日")
c3.metric("⚪ 買超觀察", f"{len(watch)} 檔",  help="外資或投信單邊買超")

st.caption("**▼ 賣超訊號**")
d1, d2, d3 = st.columns(3)
d1.metric("🔵 強賣訊號", f"{len(sell_s)} 檔", help="三大法人齊賣 或 外資連賣 ≥5 日")
d2.metric("🔷 中賣訊號", f"{len(sell_m)} 檔", help="外資+投信同賣 或 外資連賣 ≥3 日")
d3.metric("○ 賣超觀察", f"{len(sell_w)} 檔",  help="外資或投信單邊賣超")

# ── 買超訊號清單 ──────────────────────────────────────────
st.subheader("▲ 買超訊號")
if not strong.empty:
    st.markdown('<div class="signal-header-red"><strong>🔴 強買訊號</strong>　三大法人齊買 或 外資連買 ≥5 日</div>', unsafe_allow_html=True)
    render_table(strong)

if not medium.empty:
    st.markdown('<div class="signal-header-yellow"><strong>🟡 中買訊號</strong>　外資 + 投信同日買超</div>', unsafe_allow_html=True)
    render_table(medium)

if not watch.empty:
    with st.expander(f"⚪ 買超觀察（{len(watch)} 檔）"):
        render_table(watch)

# ── 賣超訊號清單 ──────────────────────────────────────────
st.subheader("▼ 賣超訊號")
SELL_COLS = {
    'stock_id':            '代號',
    'stock_name':          '名稱',
    'foreign_net':         '外資（張）',
    'trust_net':           '投信（張）',
    'dealer_net':          '自營（張）',
    'total_net':           '三大合計（張）',
    'institutional_ratio': '法人參與率',
    'foreign_sell_consec': '外資連賣天數',
}

def render_sell_table(sub_df):
    cols    = [c for c in SELL_COLS.keys() if c in sub_df.columns]
    display = sub_df[cols].rename(columns=SELL_COLS)
    if '法人參與率' in display.columns:
        display['法人參與率'] = display['法人參與率'].apply(fmt_ratio)
    st.dataframe(display, use_container_width=True, hide_index=True)

if not sell_s.empty:
    st.markdown('<div class="signal-header-blue"><strong>🔵 強賣訊號</strong>　三大法人齊賣 或 外資連賣 ≥5 日</div>', unsafe_allow_html=True)
    render_sell_table(sell_s.sort_values('total_net'))

if not sell_m.empty:
    st.markdown('<div class="signal-header-cyan"><strong>🔷 中賣訊號</strong>　外資 + 投信同日賣超</div>', unsafe_allow_html=True)
    render_sell_table(sell_m.sort_values('total_net'))

if not sell_w.empty:
    with st.expander(f"○ 賣超觀察（{len(sell_w)} 檔）"):
        render_sell_table(sell_w.sort_values('total_net'))

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

    ratio_raw = row.get('institutional_ratio', None)
    if ratio_raw is not None and str(ratio_raw) not in ('', 'nan', 'None'):
        pct = float(ratio_raw) * 100
        ratio_str = f'🔥 {pct:.1f}%' if pct >= 50 else f'{pct:.1f}%'
        st.metric(
            "📊 法人參與率",
            ratio_str,
            help="法人三大合計買賣超張數 / 當日成交量。🔥 ≥50%：法人主導行情，成交集中；<20%：散戶為主要驅動力。"
        )

    chart_col, pie_col = st.columns([2, 1])
    with chart_col:
        render_chart(stock_id, stock_name)
    with pie_col:
        render_pie(row)

    render_judge(row, date)

st.divider()
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
  <p><strong>▲ 買超訊號（法人正在進場）：</strong></p>
  <p>
    <span class="tag-red">🔴 強買訊號</span> 外資＋投信＋自營商三方同日買超，或外資連續買超 5 天以上 → 最優先關注<br>
    <span class="tag-yellow">🟡 中買訊號</span> 外資＋投信同一天都在買，或外資連買 3 天以上 → 值得追蹤<br>
    <span class="tag-grey">⚪ 買超觀察</span> 單邊法人買超（僅外資或僅投信）→ 列入候補清單
  </p>
  <p><strong>▼ 賣超訊號（法人正在出場，留意風險）：</strong></p>
  <p>
    <span style="color:#4a9eff;font-weight:600;">🔵 強賣訊號</span> 外資＋投信＋自營商三方同日賣超，或外資連續賣超 5 天以上 → 避開或減碼參考<br>
    <span style="color:#22d3ee;font-weight:600;">🔷 中賣訊號</span> 外資＋投信同一天都在賣，或外資連賣 3 天以上 → 觀察是否持續<br>
    <span style="color:#666;font-weight:600;">○ 賣超觀察</span> 單邊法人賣超 → 注意但不必過度反應
  </p>
  <p><strong>怎麼用：</strong>看訊號清單 → 選感興趣的個股 → 下方查看法人走勢圖與買賣超佔比 → 點連結核對 TWSE 官方原始資料。</p>
  <p class="warning">⚠️ 此工具為決策輔助，非買賣指令。注意：外資／投信／自營商為類別合計，TWSE 不揭露個別機構名稱。資料 T+1，每個交易日 17:30 後自動更新。</p>
</div>
""", unsafe_allow_html=True)

