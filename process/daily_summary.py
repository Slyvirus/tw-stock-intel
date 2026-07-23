"""
台股每日摘要 — Telegram 推播（每交易日發送）
一則完整市場摘要：大盤指數 → 三大法人金額 → 法人訊號亮點 → 融資斷頭監測。
融資只是其中一塊；維持率／跌停達警戒門檻時，摘要內多一段⚠️警示。

環境變數（GitHub Actions repo secrets）：
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  —— 任一缺少則安靜跳過。
資料：stocks.db（signals / margin_market，由前面步驟寫入）+ TWSE BFI82U（法人金額，即時抓）。

手動測試：python process/daily_summary.py --force
"""

import os
import sys
import sqlite3
import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / 'data' / 'stocks.db'

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
BFI82U  = 'https://www.twse.com.tw/rwd/zh/fund/BFI82U'

INITIAL_MAINTENANCE = 166.7
MARGIN_CALL_LINE     = 130.0
# 警戒門檻
DROP_YI_THRESHOLD    = 100    # 全市場融資單日減 ≥ 100 億
MAINT_THRESHOLD      = 150.0  # 上市維持率估算 ≤ 150%
LIMIT_DOWN_THRESHOLD = 30     # 上市跌停估 ≥ 30 檔

WEEKDAY = ['一', '二', '三', '四', '五', '六', '日']


def _yi(val_yuan) -> float:
    """元 → 億元"""
    return float(val_yuan) / 1e8


# ── 大盤 + 融資（讀 margin_market）────────────────────────

def _market_block(conn):
    rows = conn.execute(
        "SELECT date, margin_bal_k, margin_prev_k, index_close, turnover_k, limit_down_cnt "
        "FROM margin_market WHERE market='TWSE' ORDER BY date").fetchall()
    tpex = conn.execute(
        "SELECT margin_bal_k, margin_prev_k FROM margin_market "
        "WHERE market='TPEX' AND date=(SELECT MAX(date) FROM margin_market WHERE market='TPEX')"
    ).fetchone()
    if not rows:
        return None
    latest = rows[-1]
    date, tw_bal, tw_prev, idx, turnover, ld = latest
    prev_idx = rows[-2][3] if len(rows) >= 2 else None

    # 大盤漲跌
    idx_chg = idx_pct = None
    if idx and prev_idx:
        idx_chg = idx - prev_idx
        idx_pct = idx_chg / prev_idx * 100

    # 融資：全市場合計 + 自波段高點減幅 + 維持率估算
    tp_bal  = (tpex[0] if tpex else 0) or 0
    tp_prev = (tpex[1] if tpex else 0) or 0
    total_bal  = (tw_bal or 0) + tp_bal
    total_prev = (tw_prev or 0) + tp_prev
    day_chg_yi = (total_bal - total_prev) / 1e5

    peak_bal = max(r[1] for r in rows if r[1])
    drop_pct = (tw_bal - peak_bal) / peak_bal * 100 if peak_bal else None

    maint = None
    idx_rows = [r for r in rows if r[1] and r[3]]
    if idx and idx_rows:
        peak_row = max(idx_rows, key=lambda r: r[1])
        if peak_row[3]:
            maint = INITIAL_MAINTENANCE * idx / peak_row[3]

    return {'date': date, 'index': idx, 'idx_chg': idx_chg, 'idx_pct': idx_pct,
            'turnover_yuan': (turnover or 0), 'total_bal_yi': total_bal / 1e5,
            'day_chg_yi': day_chg_yi, 'drop_pct': drop_pct, 'maint': maint, 'ld': ld}


# ── 三大法人金額（即時抓 BFI82U）─────────────────────────

def _institutional_block(date_iso: str):
    ds = date_iso.replace('-', '')
    try:
        r = requests.get(BFI82U, params={'dayDate': ds, 'type': 'day', 'response': 'json'},
                        headers=HEADERS, timeout=15, verify=False)
        payload = r.json()
    except Exception:
        return None
    if payload.get('stat') != 'OK' or not payload.get('data'):
        return None
    foreign = trust = dealer = total = 0.0
    for row in payload['data']:
        name = row[0]
        diff = float(row[3].replace(',', '')) if len(row) > 3 else 0
        if name.startswith('外資'):
            foreign += diff
        elif name.startswith('投信'):
            trust += diff
        elif name.startswith('自營商'):
            dealer += diff
        elif name.startswith('合計'):
            total = diff
    return {'foreign': _yi(foreign), 'trust': _yi(trust),
            'dealer': _yi(dealer), 'total': _yi(total)}


# ── 法人訊號亮點（讀 signals）────────────────────────────

def _signal_block(conn, date_iso: str):
    cur = conn.execute(
        "SELECT stock_id, stock_name, foreign_net, signal_strength, sell_strength "
        "FROM signals WHERE date=?", (date_iso,))
    rows = cur.fetchall()
    if not rows:
        return None
    strong_buy  = sum(1 for r in rows if r[3] == 'strong')
    strong_sell = sum(1 for r in rows if r[4] == 'strong')
    # 只取一般個股（4 碼數字代號），排除 ETF（00 開頭高張數會洗版）
    stocks_only = [r for r in rows
                   if r[2] and r[2] > 0 and len(r[0]) == 4
                   and r[0].isdigit() and not r[0].startswith('0')]
    top_buy = sorted(stocks_only, key=lambda r: r[2], reverse=True)[:3]
    top_names = "、".join(f"{r[1]}" for r in top_buy) if top_buy else "—"
    return {'strong_buy': strong_buy, 'strong_sell': strong_sell, 'top_names': top_names}


# ── 組訊息 ────────────────────────────────────────────────

def _compose(mkt, inst, sig):
    d = datetime.date.fromisoformat(mkt['date'])
    header = f"📊 <b>台股每日摘要</b> ｜ {mkt['date']} ({WEEKDAY[d.weekday()]})"

    # 大盤
    if mkt['idx_chg'] is not None:
        arrow = '🔺' if mkt['idx_chg'] >= 0 else '🔻'
        idx_line = (f"加權指數 <b>{mkt['index']:,.0f}</b> "
                    f"{arrow}{mkt['idx_chg']:+,.0f} ({mkt['idx_pct']:+.2f}%)")
    else:
        idx_line = f"加權指數 <b>{mkt['index']:,.0f}</b>"
    big = ["📈 <b>大盤</b>", idx_line, f"成交 {mkt['turnover_yuan']/1e12:,.2f} 兆"]

    # 三大法人
    if inst:
        def s(v): return f"+{v:,.0f}" if v >= 0 else f"{v:,.0f}"
        law = ["", "🏦 <b>三大法人（億元）</b>",
               f"外資 {s(inst['foreign'])}　投信 {s(inst['trust'])}　自營 {s(inst['dealer'])}",
               f"合計 <b>{s(inst['total'])} 億</b>"]
    else:
        law = []

    # 法人訊號
    if sig:
        law += ["", "🎯 <b>法人訊號</b>",
                f"🔴 強買 {sig['strong_buy']} 檔　🔵 強賣 {sig['strong_sell']} 檔",
                f"外資買超前3：{sig['top_names']}"]

    # 融資監測
    mar = ["", "🔥 <b>融資監測</b>",
           f"餘額 {mkt['total_bal_yi']:,.0f} 億（單日 {mkt['day_chg_yi']:+,.0f}）"
           + (f"｜自高點 {mkt['drop_pct']:+.1f}%" if mkt['drop_pct'] is not None else "")]
    line2 = []
    if mkt['maint']:
        line2.append(f"維持率估 {mkt['maint']:.0f}%（追繳{MARGIN_CALL_LINE:.0f}）")
    if mkt['ld'] is not None:
        line2.append(f"跌停估 {mkt['ld']} 檔")
    if line2:
        mar.append("｜".join(line2))

    # 警戒
    warns = []
    if mkt['day_chg_yi'] <= -DROP_YI_THRESHOLD:
        warns.append(f"融資單日大減 {abs(mkt['day_chg_yi']):,.0f} 億")
    if mkt['maint'] is not None and mkt['maint'] <= MAINT_THRESHOLD:
        warns.append(f"維持率估跌至 {mkt['maint']:.0f}%")
    if mkt['ld'] is not None and mkt['ld'] >= LIMIT_DOWN_THRESHOLD:
        warns.append(f"跌停估 {mkt['ld']} 檔")
    if mkt['maint']:
        if mkt['maint'] <= 140:
            mar.append("⚠️ 維持率估逼近追繳線，去槓桿賣壓風險升高")
        elif mkt['maint'] > 155 and not warns:
            mar.append("🟢 維持率估仍在相對安全區")
    if warns:
        mar += ["", "⚠️ <b>警戒</b>：" + "、".join(warns)]

    footer = ["", "<i>金額/維持率估算供參，非投資建議。詳見 Dashboard。</i>"]
    return "\n".join(l for l in ([header] + big + law + mar + footer) if l is not None)


def _send(token, chat_id, text):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={'chat_id': chat_id, 'text': text,
                            'parse_mode': 'HTML', 'disable_web_page_preview': True},
                      timeout=15)
    r.raise_for_status()
    return r.json()


def main(force=False):
    token   = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳過推播。")
        return
    if not DB_PATH.exists():
        print("DB 不存在，跳過推播。")
        return

    conn = sqlite3.connect(DB_PATH)
    mkt = _market_block(conn)
    if not mkt:
        print("margin_market 無資料，跳過。")
        conn.close()
        return
    inst = _institutional_block(mkt['date'])
    sig  = _signal_block(conn, mkt['date'])
    conn.close()

    text = _compose(mkt, inst, sig)
    resp = _send(token, chat_id, text)
    print(f"已推播每日摘要（force={force}）：{resp.get('ok')}")


if __name__ == '__main__':
    main(force=('--force' in sys.argv))
