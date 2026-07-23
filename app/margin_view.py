"""
融資斷頭監測分頁 — 由 dashboard.py 以 tab 形式載入
資料來源：TWSE MI_MARGN / FMTQIK / STOCK_DAY_ALL、TPEx margin balance / tradingIndex
由 fetch/margin_fetch.py 每日寫入 margin_market / margin_stock 兩張表。

設計原則（對應 Shu 的判斷框架）：
  1. 融資餘額水位、日增減、自波段高點累計減幅 %  ← 精確值，判斷洗盤進度的主量尺
  2. 融資維持率估算                            ← 估算值，明確標注假設，看方向不看生死線
  3. 個股融資使用率排行                        ← 精確值，找高槓桿曝險股
  4. 跌停 / 接近跌停家數                        ← 估算值（由收盤漲跌幅推算）
本分頁是「狀態儀表」，不預測何時止跌，不產生任何進場指令。
"""

import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

INITIAL_MAINTENANCE = 166.7   # 融資成數 6 成 → 初始維持率
MARGIN_CALL_LINE    = 130.0   # 追繳線


@st.cache_data(ttl=300)
def _load_market(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM margin_market ORDER BY date", conn)
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_stock(db_path: str, market_date: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM margin_stock WHERE date=?", conn, params=(market_date,))
    conn.close()
    return df


def _estimate_maintenance(mkt_df: pd.DataFrame) -> 'float | None':
    """維持率估算 = 166.7% × (今日指數 / 融資波段高點日之指數)
    假設：現有融資部位平均在波段高點附近建立、平均融資成數 6 成。"""
    d = mkt_df.dropna(subset=['index_close'])
    d = d[d['margin_bal_k'].notna()]
    if d.empty or d['index_close'].iloc[-1] <= 0:
        return None
    peak_row  = d.loc[d['margin_bal_k'].idxmax()]
    peak_idx  = peak_row['index_close']
    today_idx = d['index_close'].iloc[-1]
    if not peak_idx or peak_idx <= 0:
        return None
    return INITIAL_MAINTENANCE * today_idx / peak_idx


def _fmt_yi(k_val) -> str:
    """仟元 → 億元字串"""
    try:
        return f'{float(k_val) / 1e5:,.0f} 億'
    except (TypeError, ValueError):
        return '—'


def render(db_path, container):
    with container:
        market = _load_market(str(db_path))
        if market.empty:
            st.info("⏳ 融資監測資料尚未建立，每交易日 17:30 後更新。")
            return

        twse = market[market['market'] == 'TWSE'].reset_index(drop=True)
        tpex = market[market['market'] == 'TPEX'].reset_index(drop=True)
        latest_date = market['date'].max()

        # ── 資料來源列 ───────────────────────────────────
        st.markdown(f"""
<div class="source-bar">
  ✅ <strong>資料來源：TWSE 融資融券彙總（MI_MARGN）＋ TPEx 上櫃信用交易</strong>
  　｜　資料日期：{latest_date}　｜　每交易日 17:30 後更新
  <a href="https://www.twse.com.tw/zh/trading/margin/MI_MARGN.html" target="_blank">→ TWSE 官方頁</a>
</div>
""", unsafe_allow_html=True)

        # ── 頂部計數卡 ───────────────────────────────────
        tw_last = twse.iloc[-1] if not twse.empty else None
        tp_last = tpex.iloc[-1] if not tpex.empty else None

        tw_bal   = tw_last['margin_bal_k'] if tw_last is not None else 0
        tw_prev  = tw_last['margin_prev_k'] if tw_last is not None else 0
        tp_bal   = tp_last['margin_bal_k'] if tp_last is not None else 0
        tp_prev  = tp_last['margin_prev_k'] if tp_last is not None else 0
        total_bal, total_prev = (tw_bal or 0) + (tp_bal or 0), (tw_prev or 0) + (tp_prev or 0)
        day_chg = total_bal - total_prev

        # 自波段高點減幅（上市，主量尺）
        peak_k = twse['margin_bal_k'].max() if not twse.empty else None
        drop_pct = (tw_bal - peak_k) / peak_k * 100 if peak_k else None

        maint = _estimate_maintenance(twse)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("融資餘額（全市場）", _fmt_yi(total_bal),
                  delta=f'{day_chg / 1e5:+,.0f} 億（單日）',
                  delta_color='inverse',
                  help="上市＋上櫃融資今日餘額合計。單日大減＝去槓桿/斷頭賣壓宣洩。")
        c2.metric("自波段高點減幅（上市）",
                  f'{drop_pct:+.1f}%' if drop_pct is not None else '—',
                  help="融資自資料區間內高點的累計減幅。歷史上完整一輪洗盤約需 -10~15% 以上。")
        c3.metric("融資維持率估算（上市）",
                  f'{maint:.0f}%' if maint else '—',
                  help=f"估算值，非真實整戶維持率。追繳線約 {MARGIN_CALL_LINE:.0f}%、初始 {INITIAL_MAINTENANCE:.0f}%。")
        ld = int(tw_last['limit_down_cnt']) if (tw_last is not None and pd.notna(tw_last['limit_down_cnt'])) else None
        c4.metric("跌停家數（上市・估算）",
                  f'{ld} 檔' if ld is not None else '—',
                  help="由個股收盤漲跌幅推算（跌幅≥9.5%），賣壓強度參考。")

        # ── 維持率警示帶 ─────────────────────────────────
        if maint:
            if maint <= 140:
                tone, msg = '#ff6b6b', '⚠️ 維持率估算已逼近追繳線，去槓桿賣壓風險升高，留意連續大減訊號。'
            elif maint <= 155:
                tone, msg = '#ffd93d', '🟡 維持率估算在警戒區，槓桿部位承壓，尚未進入大規模追繳。'
            else:
                tone, msg = '#6bcb77', '🟢 維持率估算仍在相對安全區，離追繳線有距離。'
            st.markdown(
                f'<div style="background:#161b22;border-left:4px solid {tone};'
                f'border-radius:6px;padding:10px 16px;margin:8px 0 4px;color:#ddd;'
                f'font-size:0.9rem;">{msg}</div>', unsafe_allow_html=True)

        # ── 融資餘額 vs 指數走勢 ─────────────────────────
        st.subheader("📉 融資餘額 vs 加權指數（上市）")
        if len(twse) >= 2:
            d = twse.dropna(subset=['index_close']).copy()
            d['bal_yi'] = d['margin_bal_k'] / 1e5
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=d['date'], y=d['bal_yi'], name='融資餘額（億）',
                mode='lines+markers', line=dict(color='#ff8200', width=2), yaxis='y1'))
            fig.add_trace(go.Scatter(
                x=d['date'], y=d['index_close'], name='加權指數',
                mode='lines', line=dict(color='#4a9eff', width=2, dash='dot'), yaxis='y2'))
            fig.update_layout(
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                font=dict(color='#cccccc'),
                legend=dict(orientation='h', y=1.15, bgcolor='rgba(0,0,0,0)'),
                margin=dict(t=40, b=20), height=360,
                yaxis=dict(title='融資餘額（億）', gridcolor='#2d2d2d'),
                yaxis2=dict(title='加權指數', overlaying='y', side='right', showgrid=False),
                xaxis=dict(gridcolor='#2d2d2d'))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("💡 看背離：指數大跌但融資餘額不減 → 槓桿還沒洗完；"
                       "融資連續大減且指數不再破低 → 賣壓宣洩接近尾聲。")
        else:
            st.info("📅 趨勢圖需 2 個交易日以上資料，回補後即顯示。")

        # ── 個股融資使用率排行 ───────────────────────────
        st.subheader("🔥 個股融資使用率排行（高槓桿曝險）")
        st.caption("使用率＝融資餘額 ÷ 融資限額。愈高代表該股融資盤愈擁擠，"
                   "下跌時愈容易觸發追繳與多殺多。")
        stock = _load_stock(str(db_path), latest_date)
        if not stock.empty:
            mkt_label = {'TWSE': '上市', 'TPEX': '上櫃'}
            view = stock[stock['margin_bal'] >= 2000].copy()
            view['市場']   = view['market'].map(mkt_label)
            view = view.sort_values('usage_rate', ascending=False).head(25)
            view['融資餘額（張）'] = view['margin_bal'].map(lambda x: f'{int(x):,}')
            view['使用率(%)']    = view['usage_rate'].map(lambda x: f'{float(x):.1f}%')
            view['融券餘額（張）'] = view['short_bal'].map(lambda x: f'{int(x):,}')
            show = view[['市場', 'stock_id', 'stock_name',
                         '融資餘額（張）', '使用率(%)', '融券餘額（張）']].rename(
                columns={'stock_id': '代號', 'stock_name': '名稱'})
            st.dataframe(show, use_container_width=True, hide_index=True)
            st.caption("僅列融資餘額 ≥ 2,000 張者，避免小額個股使用率失真。")
        else:
            st.info("個股融資資料尚未建立。")

        # ── 機制說明 + 估算法透明 ────────────────────────
        st.divider()
        st.markdown(f"""
<div class="guide-card">
  <h3>📖 融資斷頭是怎麼運作的？這頁在看什麼？</h3>
  <p><strong>融資＝跟券商借錢買股</strong>（自備 4 成、借 6 成）。當股價下跌，你的擔保品市值縮水，
  「整戶融資維持率」跟著下降：</p>
  <p>
    <span class="tag-yellow">維持率 &lt; 130%（追繳線）</span> → 券商電話通知「追繳」，2 個交易日內要補錢到 166% 以上<br>
    <span class="tag-red">補不足且再跌破 130%</span> → 隔日盤中<strong>強制賣出（斷頭）</strong>，不管你願不願意<br>
    <span class="tag-grey">初始維持率 166.7%</span> → 融資成數 6 成的起始水位（買進當下）
  </p>
  <p><strong>怎麼判斷這波洗到哪了（看方向，不看單一數字）：</strong></p>
  <p>
    ① <strong>融資自波段高點減幅</strong>：完整一輪去槓桿通常減 10~15% 以上，減幅還小＝槓桿未清<br>
    ② <strong>連續大減 + 指數不再破低</strong>：賣壓宣洩接近尾聲的訊號<br>
    ③ <strong>跌停家數收斂</strong>：從「賣不掉」變「賣得掉」
  </p>
  <p class="warning">⚠️ <strong>關於維持率數字的誠實話</strong>：真正的「整戶維持率」是券商內部資料、不對外公開。
  本頁的維持率為<strong>估算值</strong>，公式：166.7% ×（今日指數 ÷ 融資餘額波段高點日之指數），
  假設現有融資部位平均在高點附近建立、成數 6 成。它會系統性偏離真實值，<strong>只能看方向與相對變化，不能當生死線</strong>。
  券商圈實務估市場整戶維持率約 165~170%。</p>
  <p class="warning">⚠️ 本頁是<strong>狀態儀表，不是預測器</strong>：它告訴你「籌碼洗到哪了」，
  不會告訴你「何時止跌」或「可以進場了」。不構成投資建議。</p>
</div>
""", unsafe_allow_html=True)
