"""
融資斷頭監測 — Telegram 推播（門檻觸發式）
讀 margin_market 最新一日，達到警戒門檻才推播，避免每日洗版。

環境變數（GitHub Actions repo secrets）：
  TELEGRAM_BOT_TOKEN   BotFather 給的 token
  TELEGRAM_CHAT_ID     你的 chat id（跟 bot 對話後可查）
兩者任一缺少 → 安靜跳過（不報錯，讓排程照常）。

手動測試：python process/margin_alert.py --force   （無視門檻，強制送一則）
"""

import os
import sys
import sqlite3
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / 'data' / 'stocks.db'

# 觸發門檻
DROP_YI_THRESHOLD   = 100    # 全市場融資單日減幅 ≥ 100 億
MAINT_THRESHOLD     = 150.0  # 上市維持率估算 ≤ 150%
LIMIT_DOWN_THRESHOLD = 30    # 上市跌停估 ≥ 30 檔
INITIAL_MAINTENANCE = 166.7


def _latest_snapshot():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT market, margin_bal_k, margin_prev_k, index_close, limit_down_cnt "
        "FROM margin_market WHERE date=(SELECT MAX(date) FROM margin_market)"
    ).fetchall()
    date = conn.execute("SELECT MAX(date) FROM margin_market").fetchone()[0]
    twse_hist = conn.execute(
        "SELECT margin_bal_k, index_close FROM margin_market "
        "WHERE market='TWSE' AND margin_bal_k IS NOT NULL"
    ).fetchall()
    conn.close()

    snap = {r[0]: {'bal': r[1], 'prev': r[2], 'idx': r[3], 'ld': r[4]} for r in rows}
    # 維持率估算：166.7% × 今日指數 / 融資高點日指數
    maint = None
    if twse_hist and snap.get('TWSE', {}).get('idx'):
        peak = max(twse_hist, key=lambda x: x[0] or 0)
        if peak[1]:
            maint = INITIAL_MAINTENANCE * snap['TWSE']['idx'] / peak[1]
    # 自波段高點減幅
    drop_pct = None
    if twse_hist:
        peak_bal = max(x[0] for x in twse_hist)
        if peak_bal:
            drop_pct = (snap['TWSE']['bal'] - peak_bal) / peak_bal * 100
    return date, snap, maint, drop_pct


def _build_message(date, snap, maint, drop_pct, triggers):
    tw = snap.get('TWSE', {})
    tp = snap.get('TPEX', {})
    total_bal  = (tw.get('bal') or 0) + (tp.get('bal') or 0)
    total_prev = (tw.get('prev') or 0) + (tp.get('prev') or 0)
    day_chg = (total_bal - total_prev) / 1e5
    ld = tw.get('ld')

    lines = [
        f"🔥 <b>融資斷頭監測</b> ｜ {date}",
        f"融資餘額(全市場) <b>{total_bal/1e5:,.0f} 億</b>（單日 {day_chg:+,.0f} 億）",
        f"自波段高點(上市) {drop_pct:+.1f}%" if drop_pct is not None else "",
        f"維持率估算(上市) <b>{maint:.0f}%</b>（追繳線 130%）" if maint else "",
        f"跌停估(上市) {ld} 檔" if ld is not None else "",
    ]
    if triggers:
        lines.append("")
        lines.append("⚠️ 觸發：" + "、".join(triggers))
    lines.append("")
    lines.append("<i>估算值，看方向不看生死線；狀態儀表非預測器。</i>")
    return "\n".join(l for l in lines if l != "")


def _send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={
        'chat_id': chat_id, 'text': text,
        'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=15)
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

    date, snap, maint, drop_pct = _latest_snapshot()
    tw = snap.get('TWSE', {})
    tp = snap.get('TPEX', {})
    day_chg_yi = abs(((tw.get('bal') or 0) + (tp.get('bal') or 0)
                     - (tw.get('prev') or 0) - (tp.get('prev') or 0)) / 1e5)

    triggers = []
    net_chg = ((tw.get('bal') or 0) + (tp.get('bal') or 0)
               - (tw.get('prev') or 0) - (tp.get('prev') or 0)) / 1e5
    if net_chg <= -DROP_YI_THRESHOLD:
        triggers.append(f"融資單日大減 {day_chg_yi:,.0f} 億")
    if maint is not None and maint <= MAINT_THRESHOLD:
        triggers.append(f"維持率估算跌至 {maint:.0f}%")
    if tw.get('ld') is not None and tw['ld'] >= LIMIT_DOWN_THRESHOLD:
        triggers.append(f"跌停估 {tw['ld']} 檔")

    if not triggers and not force:
        print(f"{date} 未達推播門檻，安靜跳過。")
        return

    text = _build_message(date, snap, maint, drop_pct, triggers)
    resp = _send(token, chat_id, text)
    print(f"已推播（force={force}）：{resp.get('ok')}")


if __name__ == '__main__':
    main(force=('--force' in sys.argv))
